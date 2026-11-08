"""更新检查与安装。

发布清单 ops/releases/manifest.json 由手机管理端签名，桌面端用已绑定的管理端公钥验签；
下载文件必须与清单中的 SHA-256 一致。Windows 下通过批处理在进程退出后覆盖文件。
"""
from __future__ import annotations

import os
import platform
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import APP_VERSION, Config, install_dir
from .crypto_utils import sha256_file, verify_b64
from .github_client import GitHubClient
from .util import LOG, now_ts, parse_iso

MANIFEST_PATH = "ops/releases/manifest.json"


@dataclass
class UpdateFile:
    path: str
    url: str
    sha256: str
    size: int = 0


@dataclass
class UpdateInfo:
    version: str
    notes: str
    released_at: str
    mandatory: bool = False
    files: list[UpdateFile] = field(default_factory=list)
    signature_ok: bool = False


def version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for piece in str(value or "0").replace("-", ".").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts[:4]) or (0,)


class Updater:
    def __init__(self, config: Config, github: GitHubClient) -> None:
        self.config = config
        self.github = github
        self.last_error = ""
        self.last_check = 0.0

    def _admin_pubkey(self) -> bytes:
        key = self.config.admin_pubkey()
        if not key:
            raise RuntimeError("尚未绑定管理端公钥，无法校验更新包")
        return key

    def check(self) -> UpdateInfo | None:
        self.last_check = now_ts()
        try:
            doc = self.github.get_json(MANIFEST_PATH)
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            LOG.warning("检查更新失败：%s", exc)
            return None
        if not doc:
            self.last_error = "仓库中没有发布清单"
            return None
        if not verify_b64(self._admin_pubkey(), doc, doc.get("sig")):
            self.last_error = "更新清单签名无效"
            LOG.error(self.last_error)
            return None
        version = str(doc.get("version") or "0")
        if version_tuple(version) <= version_tuple(APP_VERSION):
            return None
        files = []
        for item in doc.get("files") or []:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            files.append(
                UpdateFile(
                    path=str(item.get("path") or Path(item["url"]).name),
                    url=str(item["url"]),
                    sha256=str(item.get("sha256") or ""),
                    size=int(item.get("size") or 0),
                )
            )
        if not files:
            self.last_error = "发布清单中没有文件"
            return None
        return UpdateInfo(
            version=version,
            notes=str(doc.get("notes") or ""),
            released_at=str(doc.get("released_at") or ""),
            mandatory=bool(doc.get("mandatory")),
            files=files,
            signature_ok=True,
        )

    def download(self, info: UpdateInfo) -> Path:
        target_root = self.config.paths()["updates"] / info.version
        target_root.mkdir(parents=True, exist_ok=True)
        for item in info.files:
            name = Path(item.path).name
            target = target_root / name
            LOG.info("下载更新文件 %s", name)
            data = self.github.download_url(item.url)
            if item.size and len(data) != item.size:
                raise RuntimeError(f"{name} 大小不一致（期望 {item.size}，实际 {len(data)}）")
            target.write_bytes(data)
            actual = sha256_file(target)
            if item.sha256 and actual.lower() != item.sha256.lower():
                target.unlink(missing_ok=True)
                raise RuntimeError(f"{name} SHA-256 校验失败")
        return target_root

    def apply(self, version: str | None = None) -> str:
        info = self.check()
        if not info:
            return self.last_error or "当前已是最新版本"
        if version and info.version != version:
            return f"清单版本 {info.version} 与请求版本 {version} 不一致"
        staging = self.download(info)
        install_to = install_dir()
        if platform.system().lower() != "windows":
            return f"更新包已下载并校验，当前系统不支持自动覆盖，目录：{staging}"
        payload = staging
        archives = list(staging.glob("*.zip"))
        if len(archives) == 1:
            payload = staging / "payload"
            payload.mkdir(exist_ok=True)
            with zipfile.ZipFile(archives[0]) as zf:
                for member in zf.infolist():
                    resolved = (payload / member.filename).resolve()
                    if not str(resolved).startswith(str(payload.resolve())):
                        raise RuntimeError("更新包包含非法路径")
                zf.extractall(payload)
        bat = staging / "apply_update.bat"
        bat.write_text(
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            "timeout /t 3 /nobreak >nul\r\n"
            "taskkill /f /im OpsDesk.exe >nul 2>&1\r\n"
            f'robocopy "{payload}" "{install_to}" /E /COPY:DAT /R:2 /W:2 >nul\r\n'
            f'start "" "{install_to / "OpsDesk.exe"}"\r\n'
            "del /f /q \"%~f0\"\r\n",
            encoding="utf-8",
        )
        subprocess.Popen(
            ["cmd.exe", "/c", str(bat)],
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            close_fds=True,
        )
        LOG.info("更新 %s 已启动安装", info.version)
        return f"更新 {info.version} 已开始安装，程序即将重启"

    def check_async_safe(self) -> dict[str, Any]:
        try:
            info = self.check()
            if not info:
                return {"available": False, "message": self.last_error or "已是最新版本"}
            return {
                "available": True,
                "version": info.version,
                "notes": info.notes,
                "released_at": info.released_at,
                "files": [f.path for f in info.files],
            }
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "message": str(exc)}


__all__ = ["Updater", "UpdateInfo", "UpdateFile", "version_tuple", "MANIFEST_PATH"]
