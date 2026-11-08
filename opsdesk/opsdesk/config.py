"""配置与数据目录。"""
from __future__ import annotations

import base64
import os
import platform
import sys
from pathlib import Path
from typing import Any

from . import builtin_keys
from .crypto_utils import aes_decrypt, aes_encrypt, b64d, b64e, derive_key, CryptoError
from .fingerprint import device_id, machine_secret
from .util import LOG, read_json, write_json_atomic

APP_NAME = "OpsDesk"
APP_TITLE = "OpsDesk 桌面运维终端"
APP_VERSION = "1.0.0"

TOKEN_SALT = b"opsdesk/token/kdf/v1"
TOKEN_AAD = b"opsdesk-token-v1"

DEFAULT_CONFIG: dict[str, Any] = {
    "repo": "le1567/-123",
    "branch": "main",
    "token_enc": "",
    "poll_interval": 60,
    "heartbeat_interval": 600,
    "license_sync_interval": 900,
    "channel_enabled": True,
    "allow_remote_script": False,
    "auto_update": False,
    "admin_pubkey": "",
    "instance_name": "",
    "api_base": "https://api.github.com",
}


def data_dir() -> Path:
    override = os.environ.get("OPSDESK_HOME")
    if override:
        base = Path(override)
    elif platform.system().lower() == "windows":
        base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def install_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


class Config:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (data_dir() / "config.json")
        self.data: dict[str, Any] = dict(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        stored = read_json(self.path, {}) or {}
        if isinstance(stored, dict):
            self.data.update({k: v for k, v in stored.items() if k in DEFAULT_CONFIG})
        if not self.data.get("instance_name"):
            self.data["instance_name"] = platform.node() or "未命名终端"

    def save(self) -> None:
        write_json_atomic(self.path, self.data)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, DEFAULT_CONFIG.get(key, default))

    def set(self, key: str, value: Any, save: bool = True) -> None:
        self.data[key] = value
        if save:
            self.save()

    # ---------- token ----------
    def _token_key(self) -> bytes:
        return derive_key(machine_secret(), TOKEN_SALT)

    def get_token(self) -> str:
        blob = self.data.get("token_enc") or ""
        if not blob:
            return ""
        try:
            return aes_decrypt(self._token_key(), b64d(blob), TOKEN_AAD).decode("utf-8")
        except (CryptoError, Exception):  # noqa: BLE001
            LOG.warning("访问令牌解密失败（可能更换了硬件），请重新填写")
            return ""

    def set_token(self, token: str) -> None:
        token = (token or "").strip()
        if not token:
            self.data["token_enc"] = ""
        else:
            self.data["token_enc"] = b64e(aes_encrypt(self._token_key(), token.encode("utf-8"), TOKEN_AAD))
        self.save()

    # ---------- 身份/密钥 ----------
    def device_id(self) -> str:
        return device_id()

    def vendor_pubkey(self) -> bytes:
        override = os.environ.get("OPSDESK_VENDOR_PUBKEY")
        if override:
            return Path(override).read_bytes()
        return builtin_keys.VENDOR_PUBLIC_KEY_PEM.encode("utf-8")

    def admin_pubkey(self) -> bytes:
        value = (self.data.get("admin_pubkey") or "").strip()
        return value.encode("utf-8") if value else b""

    def has_admin_key(self) -> bool:
        return bool(self.data.get("admin_pubkey"))

    def paths(self) -> dict[str, Path]:
        root = data_dir()
        result = {
            "root": root,
            "logs": root / "logs",
            "updates": root / "updates",
            "license": root / "license.dat",
            "config": self.path,
            "state": root / "state.json",
        }
        for key, path in result.items():
            if key in ("logs", "updates"):
                path.mkdir(parents=True, exist_ok=True)
        return result


def api_token(config: Config) -> str:
    token = config.get_token()
    if not token:
        raise RuntimeError("未配置 GitHub 访问令牌，请到「授权与设置」中填写")
    return token


def masked(token: str) -> str:
    if not token:
        return "未配置"
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}…{token[-4:]}"


def default_headers(token: str) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"{APP_NAME}/{APP_VERSION}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


__all__ = [
    "APP_NAME",
    "APP_TITLE",
    "APP_VERSION",
    "Config",
    "DEFAULT_CONFIG",
    "api_token",
    "data_dir",
    "default_headers",
    "install_dir",
    "masked",
]
