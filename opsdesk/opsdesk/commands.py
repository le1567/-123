"""远程指令白名单与执行器。

所有指令必须由管理端（手机 APK）ECDSA 私钥签名，桌面端内置公钥验签；
指令按 device_id 定向，带时间窗与去重，禁止任意 shell（除非显式开启受控脚本）。
"""
from __future__ import annotations

import platform
import subprocess
from typing import Any, Callable

from . import diagnostics
from .config import APP_VERSION, Config
from .crypto_utils import verify_b64
from .license_manager import LicenseManager, STATE_TEXT
from .util import LOG, iso, now_ts, parse_iso

COMMAND_MAX_AGE = 7 * 24 * 3600
COMMAND_FUTURE_SKEW = 5 * 60
MAX_OUTPUT = 60_000

ALLOWED_WHEN_LOCKED = {"ping", "unlock", "sysinfo", "license_status", "restart_agent"}


def _result_base(doc: dict, ok: bool, output: str, started: float) -> dict[str, Any]:
    text = output if len(output) <= MAX_OUTPUT else output[:MAX_OUTPUT] + "\n...(输出已截断)"
    return {
        "cmd_id": doc.get("cmd_id"),
        "device_id": doc.get("device_id"),
        "action": doc.get("action"),
        "ok": ok,
        "output": text,
        "started_at": iso(started),
        "finished_at": iso(),
        "duration_ms": int((now_ts() - started) * 1000),
        "hostname": platform.node(),
        "agent_version": APP_VERSION,
    }


def verify_command(doc: dict[str, Any], admin_pubkey: bytes, device_id: str) -> tuple[bool, str]:
    if not admin_pubkey:
        return False, "尚未绑定管理端公钥"
    for field in ("cmd_id", "device_id", "action", "issued_at", "sig"):
        if not doc.get(field):
            return False, f"指令缺少字段：{field}"
    if doc.get("device_id") != device_id:
        return False, "指令目标设备不匹配"
    if not verify_b64(admin_pubkey, doc, doc.get("sig")):
        return False, "指令签名无效"
    issued = parse_iso(doc.get("issued_at"))
    if issued is None:
        return False, "指令时间格式错误"
    if issued > now_ts() + COMMAND_FUTURE_SKEW:
        return False, "指令时间超前"
    if now_ts() - issued > COMMAND_MAX_AGE:
        return False, "指令已过期"
    return True, ""


class CommandExecutor:
    def __init__(
        self,
        config: Config,
        license_manager: LicenseManager,
        updater=None,
        on_locked: Callable[[], None] | None = None,
    ) -> None:
        self.config = config
        self.license = license_manager
        self.updater = updater
        self.on_locked = on_locked
        self.actions: dict[str, Callable[[dict[str, Any]], str]] = {
            "ping": self._ping,
            "sysinfo": self._sysinfo,
            "inspect": self._inspect,
            "processes": self._processes,
            "kill_process": self._kill_process,
            "services": self._services,
            "restart_service": self._restart_service,
            "disk_cleanup": self._disk_cleanup,
            "network": self._network,
            "logs": self._logs,
            "license_status": self._license_status,
            "update_check": self._update_check,
            "apply_update": self._apply_update,
            "lock": self._lock,
            "unlock": self._unlock,
            "restart_agent": self._restart_agent,
            "run_script": self._run_script,
        }

    # ------------------------------------------------------------------ 执行
    def execute(self, doc: dict[str, Any]) -> dict[str, Any]:
        started = now_ts()
        action = str(doc.get("action") or "")
        status = self.license.state()
        handler = self.actions.get(action)
        if handler is None:
            return _result_base(doc, False, f"不支持的指令：{action}", started)
        if status.locked and action not in ALLOWED_WHEN_LOCKED:
            return _result_base(
                doc, False, f"终端处于锁定状态（{status.label}），已拒绝执行 {action}", started
            )
        params = doc.get("params") or {}
        if not isinstance(params, dict):
            return _result_base(doc, False, "params 必须是对象", started)
        try:
            output = handler(params)
            LOG.info("远程指令 %s 执行成功", action)
            return _result_base(doc, True, output or "", started)
        except Exception as exc:  # noqa: BLE001
            LOG.exception("远程指令 %s 执行失败", action)
            return _result_base(doc, False, f"{type(exc).__name__}: {exc}", started)

    # ------------------------------------------------------------------ 处理器
    def _ping(self, params: dict) -> str:
        return f"pong {platform.node()} {iso()}"

    def _sysinfo(self, params: dict) -> str:
        info = diagnostics.system_info()
        status = self.license.state()
        lines = [
            f"主机: {info['hostname']}",
            f"系统: {info['os']} ({info['arch']})",
            f"用户: {info['user']}",
            f"运行时长: {info.get('uptime_seconds')} 秒",
            f"CPU: {info.get('cpu_percent')}% / {info.get('cpu_count')} 核",
            f"内存: {info.get('memory_percent')}%",
            f"授权: {status.label}（{status.summary()}）",
            f"版本: {APP_VERSION}",
        ]
        return "\n".join(lines)

    def _inspect(self, params: dict) -> str:
        import json

        return json.dumps(diagnostics.collect_inspection(), ensure_ascii=False, indent=2, default=str)

    def _processes(self, params: dict) -> str:
        keyword = str(params.get("keyword") or "")
        limit = int(params.get("limit") or 120)
        rows = diagnostics.process_list(limit=max(1, min(limit, 500)), keyword=keyword)
        lines = ["PID\tCPU%\t内存MB\t用户\t进程"]
        for row in rows:
            lines.append(f"{row['pid']}\t{row['cpu']}\t{row['mem_mb']}\t{row['user']}\t{row['name']}")
        return "\n".join(lines)

    def _kill_process(self, params: dict) -> str:
        pid = params.get("pid")
        name = params.get("name")
        if not pid and not name:
            raise ValueError("需要提供 pid 或 name")
        return diagnostics.kill_process(pid=int(pid) if pid else None, name=str(name) if name else None)

    def _services(self, params: dict) -> str:
        keyword = str(params.get("keyword") or "").lower()
        rows = diagnostics.list_services(limit=600)
        lines = ["名称\t状态\t启动类型"]
        for row in rows:
            if keyword and keyword not in row["name"].lower() and keyword not in row["display"].lower():
                continue
            lines.append(f"{row['name']}\t{row['status']}\t{row['start_type']}")
        return "\n".join(lines[:400])

    def _restart_service(self, params: dict) -> str:
        name = str(params.get("name") or "")
        return diagnostics.restart_service(name)

    def _disk_cleanup(self, params: dict) -> str:
        hours = int(params.get("older_than_hours") or 24)
        result = diagnostics.cleanup_temp(older_than_hours=hours)
        return (
            f"清理完成：删除 {result['removed_files']} 个文件，释放 {result['freed_text']}，"
            f"失败 {result['failed_files']} 个。目录：{', '.join(result['temp_dirs'])}"
        )

    def _network(self, params: dict) -> str:
        import json

        return json.dumps(diagnostics.network_info(), ensure_ascii=False, indent=2, default=str)

    def _logs(self, params: dict) -> str:
        lines = max(1, min(int(params.get("lines") or 200), 1000))
        path = self.config.paths()["logs"] / "opsdesk.log"
        if not path.exists():
            return "暂无日志"
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.readlines()
        return "".join(content[-lines:])

    def _license_status(self, params: dict) -> str:
        status = self.license.state()
        import json

        return json.dumps(
            {
                "state": status.state,
                "label": STATE_TEXT.get(status.state, status.state),
                "summary": status.summary(),
                "remaining_offline": status.remaining_offline,
                "last_online": iso(status.last_online) if status.last_online else "",
                "device_id": self.license.device_id,
                **self.license.info(),
            },
            ensure_ascii=False,
            indent=2,
        )

    def _update_check(self, params: dict) -> str:
        if not self.updater:
            return "更新模块不可用"
        info = self.updater.check()
        if not info:
            return "当前已是最新版本"
        return (
            f"发现新版本 {info.version}（当前 {APP_VERSION}）\n"
            f"发布时间：{info.released_at}\n说明：{info.notes}\n"
            f"文件：{', '.join(f.path for f in info.files)}"
        )

    def _apply_update(self, params: dict) -> str:
        if not self.updater:
            return "更新模块不可用"
        version = str(params.get("version") or "")
        return self.updater.apply(version or None)

    def _lock(self, params: dict) -> str:
        reason = str(params.get("reason") or "管理端远程锁定")
        self.license.set_remote_lock(True, reason)
        LOG.warning("终端已被远程锁定：%s", reason)
        if self.on_locked:
            self.on_locked()
        return f"已锁定：{reason}"

    def _unlock(self, params: dict) -> str:
        self.license.set_remote_lock(False, "")
        LOG.info("终端已被管理端解锁")
        return "已解除锁定"

    def _restart_agent(self, params: dict) -> str:
        return "重启指令已记录，请通过本地服务/计划任务重启进程"

    def _run_script(self, params: dict) -> str:
        if not self.config.get("allow_remote_script"):
            raise PermissionError("远程脚本开关未开启（allow_remote_script=false）")
        script = str(params.get("script") or "").strip()
        if not script:
            raise ValueError("缺少 script 参数")
        args = [script] + [str(x) for x in (params.get("args") or [])]
        timeout = max(1, min(int(params.get("timeout") or 30), 300))
        LOG.warning("执行受控脚本：%s", args)
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            shell=False,
            cwd=str(params.get("cwd") or "") or None,
        )
        return (proc.stdout or "") + (proc.stderr or "")


__all__ = ["CommandExecutor", "verify_command", "ALLOWED_WHEN_LOCKED"]
