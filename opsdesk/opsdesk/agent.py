"""后台代理：授权联网校验、心跳上报、远程指令轮询与结果回传。"""
from __future__ import annotations

import json
import platform
import threading
import time
from typing import Any, Callable

from . import diagnostics
from .commands import CommandExecutor, verify_command
from .config import APP_VERSION, Config
from .github_client import GitHubClient, GitHubError, OfflineError
from .license_manager import LicenseManager, STATE_TEXT
from .util import LOG, iso, now_ts


class Agent:
    def __init__(
        self,
        config: Config,
        license_manager: LicenseManager,
        github: GitHubClient,
        executor: CommandExecutor,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = config
        self.license = license_manager
        self.github = github
        self.executor = executor
        self.on_event = on_event or (lambda *_: None)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self.status: dict[str, Any] = {
            "online": False,
            "last_poll": 0.0,
            "last_sync": 0.0,
            "last_heartbeat": 0.0,
            "last_error": "",
            "pending_commands": 0,
            "executed_commands": 0,
            "running": False,
        }

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="opsdesk-agent", daemon=True)
        self._thread.start()
        self.status["running"] = True
        LOG.info("后台代理已启动")

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3)
        self.status["running"] = False

    def wake(self) -> None:
        self._wake.set()

    def _emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        try:
            self.on_event(event, payload or {})
        except Exception:  # noqa: BLE001
            LOG.exception("代理事件回调失败")

    # ------------------------------------------------------------------ 主循环
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._cycle()
            except Exception as exc:  # noqa: BLE001
                LOG.exception("代理循环异常")
                self._set_status(last_error=str(exc))
            interval = max(15, int(self.config.get("poll_interval") or 60))
            self._wake.wait(timeout=interval)
            self._wake.clear()

    def _cycle(self) -> None:
        self.license.tick()
        status = self.license.state()
        self._emit("license", self._license_payload(status))
        self._set_status(running=True)
        if not self.config.get("channel_enabled"):
            self._set_status(online=False, last_error="远程通道已关闭")
            return
        if not self.config.get_token():
            self._set_status(online=False, last_error="未配置 GitHub 访问令牌")
            return
        now = now_ts()
        sync_interval = int(self.config.get("license_sync_interval") or 900)
        if status.locked:
            sync_interval = min(sync_interval, 300)
        if now - float(self.status.get("last_sync") or 0) >= sync_interval:
            self._sync_license()
            status = self.license.state()
        if now - float(self.status.get("last_heartbeat") or 0) >= int(
            self.config.get("heartbeat_interval") or 600
        ):
            self._heartbeat(status)
        self._poll_commands()
        self._set_status(online=True, last_poll=now, last_error="")

    # ------------------------------------------------------------------ 授权
    def _sync_license(self) -> None:
        try:
            before = self.license.state().state
            status = self.license.online_sync()
            after = status.state
            self._set_status(last_sync=now_ts(), last_error=self.license.last_error)
            self._emit("license", self._license_payload(status))
            if before != after:
                LOG.info("授权状态变化：%s -> %s", before, after)
                self._emit("license_changed", self._license_payload(status))
        except OfflineError as exc:
            self._set_status(last_error=str(exc), online=False)
        except Exception as exc:  # noqa: BLE001
            LOG.exception("授权同步失败")
            self._set_status(last_error=str(exc))

    def _license_payload(self, status) -> dict[str, Any]:
        return {
            "state": status.state,
            "label": STATE_TEXT.get(status.state, status.state),
            "summary": status.summary(),
            "locked": status.locked,
            "remaining_offline": status.remaining_offline,
            "remaining_license": status.remaining_license,
            "last_online": iso(status.last_online) if status.last_online else "",
            "license": self.license.info(),
        }

    # ------------------------------------------------------------------ 心跳
    def _heartbeat(self, status) -> None:
        info = diagnostics.system_info()
        payload = {
            "device_id": self.license.device_id,
            "hostname": info.get("hostname") or platform.node(),
            "instance_name": self.config.get("instance_name"),
            "os": info.get("os"),
            "arch": info.get("arch"),
            "version": APP_VERSION,
            "last_seen": iso(),
            "last_seen_ts": int(now_ts()),
            "license": {
                "state": status.state,
                "label": STATE_TEXT.get(status.state, status.state),
                "summary": status.summary(),
                "expires_at": (status.license or {}).get("expires_at", ""),
                "license_id": (status.license or {}).get("license_id", ""),
                "customer": (status.license or {}).get("customer", ""),
                "remaining_offline": status.remaining_offline,
            },
            "remote_locked": bool(self.license.store.get("remote_locked")),
            "admin_key_bound": self.config.has_admin_key(),
            "ips": [item["ip"] for item in info.get("local_ips", [])],
            "uptime_seconds": info.get("uptime_seconds"),
            "memory_percent": info.get("memory_percent"),
            "cpu_percent": info.get("cpu_percent"),
            "channel": "github",
            "poll_interval": int(self.config.get("poll_interval") or 60),
            "allow_remote_script": bool(self.config.get("allow_remote_script")),
        }
        path = f"ops/devices/{self.license.device_id}.json"
        try:
            existing = self.github.get_file(path)
            self.github.put_json(
                path,
                payload,
                f"heartbeat: {payload['device_id']} {payload['last_seen']}",
                sha=existing["sha"] if existing else None,
            )
            self._set_status(last_heartbeat=now_ts())
            self._emit("heartbeat", payload)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("心跳上报失败：%s", exc)
            self._set_status(last_error=f"心跳失败：{exc}")

    # ------------------------------------------------------------------ 指令
    def _poll_commands(self) -> None:
        path = f"ops/commands/{self.license.device_id}"
        try:
            items = self.github.list_dir(path)
        except OfflineError as exc:
            self._set_status(online=False, last_error=str(exc))
            return
        except GitHubError as exc:
            self._set_status(last_error=str(exc))
            return
        commands = [item for item in items if str(item.get("name", "")).endswith(".json")]
        self._set_status(pending_commands=len(commands))
        if not commands:
            return
        admin_key = self.config.admin_pubkey()
        for item in sorted(commands, key=lambda x: x.get("name", "")):
            if self._stop.is_set():
                break
            cmd_path = f"{path}/{item['name']}"
            try:
                doc = self.github.get_json(cmd_path)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("读取指令失败 %s：%s", cmd_path, exc)
                continue
            if not isinstance(doc, dict):
                self._delete_command(cmd_path, item.get("sha", ""))
                continue
            cmd_id = str(doc.get("cmd_id") or item["name"])
            ok, reason = verify_command(doc, admin_key, self.license.device_id)
            if not ok:
                LOG.warning("拒绝指令 %s：%s", cmd_id, reason)
                self._write_result(
                    {
                        "cmd_id": cmd_id,
                        "device_id": self.license.device_id,
                        "action": doc.get("action", ""),
                        "ok": False,
                        "output": f"指令校验失败：{reason}",
                        "finished_at": iso(),
                        "hostname": platform.node(),
                        "agent_version": APP_VERSION,
                    }
                )
                self._delete_command(cmd_path, item.get("sha", ""))
                continue
            if self.license.is_executed(cmd_id):
                self._delete_command(cmd_path, item.get("sha", ""))
                continue
            LOG.info("执行远程指令 %s (%s)", cmd_id, doc.get("action"))
            self._emit("command_start", {"cmd_id": cmd_id, "action": doc.get("action")})
            result = self.executor.execute(doc)
            self.license.mark_executed(cmd_id)
            self._write_result(result)
            self._delete_command(cmd_path, item.get("sha", ""))
            self._set_status(executed_commands=int(self.status.get("executed_commands") or 0) + 1)
            self._emit("command_done", result)
            if doc.get("action") in ("lock", "unlock"):
                self._emit("license", self._license_payload(self.license.state()))

    def _write_result(self, result: dict[str, Any]) -> None:
        path = f"ops/results/{self.license.device_id}/{result.get('cmd_id')}.json"
        try:
            existing = self.github.get_file(path)
            self.github.put_json(
                path,
                result,
                f"result: {result.get('cmd_id')} {result.get('action')}",
                sha=existing["sha"] if existing else None,
            )
        except Exception as exc:  # noqa: BLE001
            LOG.error("指令结果回传失败：%s", exc)
            self._set_status(last_error=f"结果回传失败：{exc}")

    def _delete_command(self, path: str, sha: str) -> None:
        if not sha:
            return
        try:
            self.github.delete_file(path, "chore: consume command", sha)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("删除指令失败 %s：%s", path, exc)

    # ------------------------------------------------------------------ 状态
    def _set_status(self, **kwargs: Any) -> None:
        with self._state_lock:
            self.status.update(kwargs)
        self._emit("status", self.snapshot())

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return dict(self.status)

    def sync_now(self) -> None:
        self._sync_license()
        status = self.license.state()
        self._heartbeat(status)
        self._poll_commands()
        self._set_status(last_poll=now_ts(), online=True)


__all__ = ["Agent"]
