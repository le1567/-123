"""机器指纹与设备 ID。

Windows 采集 MachineGuid + 系统盘卷序列号 + 主网卡 MAC；
macOS 采集 IOPlatformUUID；Linux 采集 machine-id。
组合后 SHA-256，生成稳定的设备码（重装系统会变化，属于预期行为）。
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import platform
import subprocess
import uuid
from pathlib import Path

APP_NAMESPACE = "OpsDesk/v1"


def _windows_machine_guid() -> str:
    try:
        import winreg

        access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, access) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(value)
    except Exception:
        return ""


def _windows_volume_serial(drive: str = "C:\\") -> str:
    try:
        root = ctypes.windll.kernel32.GetLogicalDriveStringsW  # type: ignore[attr-defined]
        serial = ctypes.c_ulong()
        max_component = ctypes.c_ulong()
        fs_flags = ctypes.c_ulong()
        volume_name = ctypes.create_unicode_buffer(261)
        fs_name = ctypes.create_unicode_buffer(261)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(drive),
            volume_name,
            261,
            ctypes.byref(serial),
            ctypes.byref(max_component),
            ctypes.byref(fs_flags),
            fs_name,
            261,
        )
        _ = root
        return f"{serial.value:08X}" if ok else ""
    except Exception:
        return ""


def _mac_platform_uuid() -> str:
    try:
        out = subprocess.check_output(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], text=True, timeout=5
        )
        for line in out.splitlines():
            if "IOPlatformUUID" in line:
                return line.split('"')[-2]
    except Exception:
        pass
    return ""


def _linux_machine_id() -> str:
    for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            text = Path(candidate).read_text(encoding="utf-8").strip()
            if text:
                return text
        except Exception:
            continue
    return ""


def _primary_mac() -> str:
    node = uuid.getnode()
    if (node >> 40) & 1:  # 随机 MAC（无真实网卡）
        return ""
    return f"{node:012X}"


def raw_components() -> dict[str, str]:
    system = platform.system().lower()
    parts = {
        "hostname": platform.node(),
        "machine": platform.machine(),
        "mac": _primary_mac(),
    }
    if system == "windows":
        parts["machine_guid"] = _windows_machine_guid()
        parts["volume_serial"] = _windows_volume_serial("C:\\")
    elif system == "darwin":
        parts["platform_uuid"] = _mac_platform_uuid()
    else:
        parts["machine_id"] = _linux_machine_id()
    return parts


def machine_fingerprint() -> str:
    parts = raw_components()
    # 只用稳定字段；hostname 允许变化，所以不参与指纹
    stable = "|".join(
        [
            APP_NAMESPACE,
            parts.get("machine_guid") or parts.get("platform_uuid") or parts.get("machine_id") or "",
            parts.get("volume_serial", ""),
            parts.get("mac", ""),
            parts.get("machine", ""),
        ]
    )
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def device_id() -> str:
    digest = machine_fingerprint().upper()
    return "OPS-" + "-".join(digest[i : i + 8] for i in range(0, 24, 8))


def machine_secret() -> str:
    """本地加密密钥的派生材料（包含指纹，拷贝到其他机器无法解密）。"""
    return hashlib.sha256(
        f"{APP_NAMESPACE}|{machine_fingerprint()}|{platform.node()}".encode("utf-8")
    ).hexdigest()
