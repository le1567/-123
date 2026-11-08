"""授权与锁定逻辑。

规则：
- 授权文件必须由厂商 ECDSA 私钥签名，且 device_id 与本机一致；
- 每 15 分钟（可配置）联网校验一次授权/吊销状态；
- 连续 72 小时无法完成联网校验 -> 锁定，桌面端主功能不可用；
- 系统时间被回拨（当前时间明显早于历史最大时间）-> 判定篡改并锁定；
- 本地授权与状态使用机器指纹派生的 AES-GCM 加密，复制到其他机器无法解密。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from cryptography.exceptions import InvalidTag

from .crypto_utils import (
    CryptoError,
    aes_decrypt,
    aes_encrypt,
    b64e,
    derive_key,
    verify_b64,
)
from .fingerprint import machine_secret
from .github_client import GitHubClient, OfflineError
from .util import LOG, human_duration, iso, now_ts, parse_iso, utc_now

OFFLINE_LIMIT_SECONDS = 3 * 24 * 3600  # 72 小时
CLOCK_SKEW_TOLERANCE = 5 * 60  # 5 分钟
STORE_SALT = b"opsdesk/license/kdf/v1"
MAX_EXECUTED_IDS = 500

STATE_ACTIVE = "active"
STATE_GRACE = "grace"
STATE_OFFLINE_LOCKED = "offline_locked"
STATE_EXPIRED = "expired"
STATE_REVOKED = "revoked"
STATE_MISSING = "missing"
STATE_INVALID = "invalid"
STATE_TAMPERED = "tampered"
STATE_CLOCK_TAMPER = "clock_tamper"
STATE_REMOTE_LOCKED = "remote_locked"

STATE_TEXT = {
    STATE_ACTIVE: "授权正常",
    STATE_GRACE: "离线宽限期",
    STATE_OFFLINE_LOCKED: "离线超时已锁定",
    STATE_EXPIRED: "授权已到期",
    STATE_REVOKED: "授权已吊销",
    STATE_MISSING: "未激活",
    STATE_INVALID: "授权文件无效",
    STATE_TAMPERED: "本地授权数据异常",
    STATE_CLOCK_TAMPER: "检测到系统时间回拨",
    STATE_REMOTE_LOCKED: "已被管理端远程锁定",
}

LOCKED_STATES = {
    STATE_OFFLINE_LOCKED,
    STATE_EXPIRED,
    STATE_REVOKED,
    STATE_MISSING,
    STATE_INVALID,
    STATE_TAMPERED,
    STATE_CLOCK_TAMPER,
    STATE_REMOTE_LOCKED,
}


@dataclass
class LicenseStatus:
    state: str
    message: str = ""
    remaining_offline: int = 0
    remaining_license: int = 0
    last_online: float | None = None
    license: dict[str, Any] | None = None
    remote_locked: bool = False
    feature_list: list[str] = field(default_factory=list)

    @property
    def locked(self) -> bool:
        return self.state in LOCKED_STATES

    @property
    def label(self) -> str:
        return STATE_TEXT.get(self.state, self.state)

    def summary(self) -> str:
        if self.state == STATE_ACTIVE:
            return f"{self.label} · 有效期剩余 {human_duration(self.remaining_license)}"
        if self.state == STATE_GRACE:
            return f"{self.label} · 距锁定还剩 {human_duration(self.remaining_offline)}"
        if self.state == STATE_OFFLINE_LOCKED:
            return f"{self.label} · 请联网校验或导入新授权"
        if self.state == STATE_REMOTE_LOCKED:
            return f"{self.label} · 请在手机端解除锁定"
        return self.label if not self.message else f"{self.label}：{self.message}"


class LicenseManager:
    def __init__(
        self,
        config: Config,
        device_id: str | None = None,
        vendor_pubkey: bytes | None = None,
        github: GitHubClient | None = None,
    ) -> None:
        self.config = config
        self.device_id = device_id or config.device_id()
        self.vendor_pubkey = vendor_pubkey or config.vendor_pubkey()
        self.github = github or GitHubClient(
            config.get("repo"),
            config.get_token(),
            config.get("branch"),
            config.get("api_base"),
        )
        self.store_path: Path = config.paths()["license"]
        self._key = derive_key(machine_secret(), STORE_SALT)
        self._aad = f"opsdesk-license|{self.device_id}".encode("utf-8")
        self.corrupt = False
        self.last_error = ""
        self.last_sync_at = 0.0
        self.store: dict[str, Any] = self._load_store()

    # ------------------------------------------------------------ 本地存储
    def _load_store(self) -> dict[str, Any]:
        if not self.store_path.exists():
            return {}
        try:
            blob = self.store_path.read_bytes()
            plain = aes_decrypt(self._key, blob, self._aad)
            data = json.loads(plain.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (CryptoError, InvalidTag, ValueError, OSError, json.JSONDecodeError) as exc:
            self.corrupt = True
            LOG.error("授权本地数据无法解密：%s", exc)
            return {}

    def _save_store(self) -> None:
        payload = json.dumps(self.store, ensure_ascii=False, sort_keys=True).encode("utf-8")
        blob = aes_encrypt(self._key, payload, self._aad)
        tmp = self.store_path.with_suffix(".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(blob)
        tmp.replace(self.store_path)

    # ------------------------------------------------------------ 校验
    def verify_document(self, doc: dict[str, Any]) -> tuple[bool, str]:
        if not isinstance(doc, dict):
            return False, "授权格式错误"
        required = ("license_id", "device_id", "issued_at", "expires_at", "sig")
        for key in required:
            if not doc.get(key):
                return False, f"授权缺少字段：{key}"
        if doc.get("device_id") != self.device_id:
            return False, "授权绑定的设备码与本机不一致"
        if not verify_b64(self.vendor_pubkey, doc, doc.get("sig")):
            return False, "授权签名校验失败"
        issued_at = parse_iso(doc.get("issued_at"))
        expires_at = parse_iso(doc.get("expires_at"))
        if issued_at is None or expires_at is None:
            return False, "授权时间格式错误"
        if issued_at > now_ts() + CLOCK_SKEW_TOLERANCE:
            return False, "授权签发时间异常"
        if expires_at <= now_ts():
            return False, "授权已过期"
        return True, ""

    def activate(self, document: dict[str, Any] | str, mark_online: bool = True) -> tuple[bool, str]:
        if isinstance(document, str):
            try:
                document = json.loads(document)
            except json.JSONDecodeError as exc:
                return False, f"授权内容不是合法 JSON：{exc}"
        ok, reason = self.verify_document(document)
        if not ok:
            return False, reason
        self.store["license"] = document
        self.store["revoked"] = False
        self.store["revoked_reason"] = ""
        if mark_online:
            self.store["last_ok_at"] = now_ts()
        self._save_store()
        LOG.info("授权激活成功：%s", document.get("license_id"))
        return True, "授权激活成功"

    def online_sync(self) -> LicenseStatus:
        """联网校验授权与吊销列表；失败时保留本地授权并进入宽限期。"""
        self.last_sync_at = now_ts()
        try:
            license_path = f"ops/licenses/{self.device_id}.json"
            doc = self.github.get_json(license_path)
            revoked = self.github.get_json("ops/revoked.json") or []
            if isinstance(revoked, dict):
                revoked = revoked.get("revoked", [])
            revoked_entry = None
            if isinstance(revoked, list):
                for entry in revoked:
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("device_id") == self.device_id or (
                        self.store.get("license")
                        and entry.get("license_id") == self.store["license"].get("license_id")
                    ):
                        revoked_entry = entry
                        break
            if revoked_entry:
                self.store["revoked"] = True
                self.store["revoked_reason"] = revoked_entry.get("reason", "已被吊销")
                self.store["last_ok_at"] = now_ts()
                self._save_store()
                LOG.warning("授权已被吊销：%s", self.store["revoked_reason"])
                return self.state()
            if doc is None:
                self.last_error = "仓库中未找到本机授权文件"
                if not self.store.get("license"):
                    return self.state()
                # 本地已有授权，但远端文件缺失：不立即锁定，等宽限期结束
                if self._last_ok_age() > OFFLINE_LIMIT_SECONDS:
                    return self.state()
                return self.state()
            ok, reason = self.verify_document(doc)
            if not ok:
                self.last_error = reason
                return self.state()
            self.store["license"] = doc
            self.store["revoked"] = False
            self.store["revoked_reason"] = ""
            self.store["last_ok_at"] = now_ts()
            self.store["max_seen_at"] = max(self.store.get("max_seen_at", 0), now_ts())
            self._save_store()
            self.last_error = ""
            LOG.info("授权联网校验通过，有效期至 %s", doc.get("expires_at"))
            return self.state()
        except OfflineError as exc:
            self.last_error = str(exc)
            LOG.warning("授权联网校验失败（离线）：%s", exc)
            return self.state()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            LOG.exception("授权联网校验异常")
            return self.state()

    # ------------------------------------------------------------ 状态
    def _last_ok_age(self) -> float:
        last = self.store.get("last_ok_at")
        return float("inf") if not last else max(0.0, now_ts() - float(last))

    def state(self) -> LicenseStatus:
        if self.corrupt:
            return LicenseStatus(STATE_TAMPERED, "授权状态文件损坏或被复制到其他设备", license=None)
        doc = self.store.get("license")
        if not doc:
            return LicenseStatus(STATE_MISSING, "请导入授权文件或联网激活", license=None)
        ok, reason = self.verify_document(doc)
        if not ok:
            state = STATE_EXPIRED if "过期" in reason else STATE_INVALID
            return LicenseStatus(state, reason, license=doc)
        expires_at = parse_iso(doc.get("expires_at")) or 0
        remaining_license = int(expires_at - now_ts())
        if doc.get("max_offline_hours"):
            try:
                limit = min(int(doc["max_offline_hours"]) * 3600, OFFLINE_LIMIT_SECONDS)
            except Exception:
                limit = OFFLINE_LIMIT_SECONDS
        else:
            limit = OFFLINE_LIMIT_SECONDS
        last_ok = self.store.get("last_ok_at")
        status = LicenseStatus(
            state=STATE_ACTIVE,
            remaining_offline=max(0, int(limit - self._last_ok_age())),
            remaining_license=remaining_license,
            last_online=float(last_ok) if last_ok else None,
            license=doc,
            remote_locked=bool(self.store.get("remote_locked")),
            feature_list=list(doc.get("features") or []),
        )
        if self.store.get("revoked"):
            status.state = STATE_REVOKED
            status.message = self.store.get("revoked_reason", "")
            return status
        if status.remote_locked:
            status.state = STATE_REMOTE_LOCKED
            status.message = self.store.get("lock_reason", "管理端已锁定")
            return status
        max_seen = float(self.store.get("max_seen_at") or 0)
        if max_seen and now_ts() + CLOCK_SKEW_TOLERANCE < max_seen:
            status.state = STATE_CLOCK_TAMPER
            status.message = f"当前时间早于历史记录 {human_duration(max_seen - now_ts())}"
            return status
        age = self._last_ok_age()
        if last_ok is None or age > limit:
            status.state = STATE_OFFLINE_LOCKED
            status.message = f"已超过 {human_duration(limit)} 未联网校验"
            return status
        if age > 3600:
            status.state = STATE_GRACE
            status.message = f"最近一次联网校验：{human_duration(age)}前"
        return status

    def tick(self) -> None:
        """每次运行刷新最大时间戳，用于检测时钟回拨。"""
        current = now_ts()
        max_seen = float(self.store.get("max_seen_at") or 0)
        if current > max_seen + 5:
            self.store["max_seen_at"] = current
            last_save = float(self.store.get("_tick_saved") or 0)
            if current - last_save > 60:
                self.store["_tick_saved"] = current
                self._save_store()

    def set_remote_lock(self, locked: bool, reason: str = "") -> None:
        self.store["remote_locked"] = bool(locked)
        self.store["lock_reason"] = reason
        self._save_store()

    def is_executed(self, command_id: str) -> bool:
        return command_id in (self.store.get("executed") or [])

    def mark_executed(self, command_id: str) -> None:
        executed = list(self.store.get("executed") or [])
        executed.append(command_id)
        self.store["executed"] = executed[-MAX_EXECUTED_IDS:]
        self._save_store()

    def info(self) -> dict[str, Any]:
        doc = self.store.get("license") or {}
        return {
            "license_id": doc.get("license_id", ""),
            "customer": doc.get("customer", ""),
            "plan": doc.get("plan", ""),
            "features": doc.get("features", []),
            "issued_at": doc.get("issued_at", ""),
            "expires_at": doc.get("expires_at", ""),
            "max_offline_hours": 72,
            "device_id": self.device_id,
            "last_online": iso(self.store.get("last_ok_at")) if self.store.get("last_ok_at") else "",
            "remote_locked": bool(self.store.get("remote_locked")),
        }

    def seconds_since_sync(self) -> float:
        return self._last_ok_age()


def demo_license_document(device_id_value: str, private_pem: bytes, days: int = 365, customer: str = "演示客户") -> dict:
    from .crypto_utils import sign_b64

    now = utc_now()
    doc = {
        "license_id": f"LIC-{int(time.time())}",
        "device_id": device_id_value,
        "customer": customer,
        "plan": "enterprise",
        "features": ["inspect", "remote", "update", "remote_lock"],
        "issued_at": iso(now.timestamp()),
        "expires_at": iso(now.timestamp() + days * 86400),
        "max_offline_hours": 72,
    }
    doc["sig"] = sign_b64(private_pem, doc)
    return doc


__all__ = [
    "LicenseManager",
    "LicenseStatus",
    "OFFLINE_LIMIT_SECONDS",
    "STATE_ACTIVE",
    "STATE_GRACE",
    "STATE_TEXT",
    "demo_license_document",
    "b64e",
]
