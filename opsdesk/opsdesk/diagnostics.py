"""桌面运维采集与操作能力（巡检、进程、服务、网络、临时文件清理）。"""
from __future__ import annotations

import getpass
import os
import platform
import re
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    import psutil  # type: ignore
except Exception:  # noqa: BLE001
    psutil = None  # type: ignore

from .util import human_bytes

SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
IS_WINDOWS = platform.system().lower() == "windows"


def _run(args: list[str], timeout: int = 10) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            shell=False,
            text=True,
            errors="replace",
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, "命令不存在"
    except subprocess.TimeoutExpired:
        return 124, "命令执行超时"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


def system_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "os_version": platform.version(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "user": _safe(getpass.getuser, ""),
        "boot_time": None,
        "uptime_seconds": None,
        "cpu_count": os.cpu_count(),
        "cpu_percent": None,
        "memory_total": None,
        "memory_used": None,
        "memory_percent": None,
        "local_ips": [],
        "mac": "",
    }
    if psutil:
        info["boot_time"] = psutil.boot_time()
        info["uptime_seconds"] = int(time.time() - psutil.boot_time())
        info["cpu_percent"] = psutil.cpu_percent(interval=0.3)
        mem = psutil.virtual_memory()
        info["memory_total"] = mem.total
        info["memory_used"] = mem.used
        info["memory_percent"] = mem.percent
        macs = []
        for nic, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    info["local_ips"].append({"interface": nic, "ip": addr.address})
                if getattr(addr, "family", None) == psutil.AF_LINK and not macs:
                    macs.append(f"{nic}={addr.address}")
        info["mac"] = ", ".join(macs[:3])
    else:
        info["local_ips"] = [{"interface": "default", "ip": socket.gethostbyname(socket.gethostname())}]
    return info


def disk_info() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if psutil:
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except Exception:  # noqa: BLE001
                continue
            rows.append(
                {
                    "device": part.device,
                    "mount": part.mountpoint,
                    "fstype": part.fstype,
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": usage.percent,
                }
            )
        return rows
    total, used, free = shutil.disk_usage("/")
    rows.append(
        {
            "device": "/",
            "mount": "/",
            "fstype": "unknown",
            "total": total,
            "used": used,
            "free": free,
            "percent": round(used / total * 100, 1),
        }
    )
    return rows


def process_list(limit: int = 300, keyword: str = "") -> list[dict[str, Any]]:
    if not psutil:
        return []
    keyword = (keyword or "").lower()
    rows: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_info", "create_time", "exe"]):
        try:
            info = proc.info
            name = info.get("name") or ""
            if keyword and keyword not in name.lower() and keyword not in str(info.get("pid")):
                continue
            mem = info.get("memory_info")
            rows.append(
                {
                    "pid": info.get("pid"),
                    "name": name,
                    "user": info.get("username") or "",
                    "cpu": round(info.get("cpu_percent") or 0.0, 1),
                    "mem_mb": round((mem.rss if mem else 0) / 1024 / 1024, 1),
                    "created": info.get("create_time"),
                    "exe": info.get("exe") or "",
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):  # noqa: BLE001
            continue
    rows.sort(key=lambda item: (item["mem_mb"], item["cpu"]), reverse=True)
    return rows[:limit]


def top_processes(count: int = 5) -> list[dict[str, Any]]:
    return process_list(limit=count)


def list_services(limit: int = 500) -> list[dict[str, Any]]:
    if IS_WINDOWS:
        code, out = _run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-Service | Select-Object Name,DisplayName,Status,StartType | ConvertTo-Json -Compress",
            ],
            timeout=20,
        )
        if code != 0 or not out.strip():
            return []
        import json

        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return []
        if isinstance(data, dict):
            data = [data]
        rows = []
        for item in data[:limit]:
            rows.append(
                {
                    "name": item.get("Name", ""),
                    "display": item.get("DisplayName", ""),
                    "status": str(item.get("Status", "")),
                    "start_type": str(item.get("StartType", "")),
                }
            )
        return rows
    if platform.system().lower() == "linux":
        code, out = _run(["systemctl", "list-units", "--type=service", "--all", "--no-pager", "--no-legend"], 15)
        rows = []
        for line in out.splitlines():
            cols = line.split()
            if len(cols) < 4:
                continue
            rows.append(
                {
                    "name": cols[0],
                    "display": cols[0],
                    "status": cols[3],
                    "start_type": " ".join(cols[4:]),
                }
            )
        return rows[:limit]
    return []


def kill_process(pid: int | None = None, name: str | None = None) -> str:
    if not psutil:
        raise RuntimeError("缺少 psutil，无法管理进程")
    targets = []
    if pid:
        targets = [psutil.Process(int(pid))]
    elif name:
        for proc in psutil.process_iter(["name"]):
            if (proc.info.get("name") or "").lower() == name.lower():
                targets.append(proc)
    if not targets:
        raise RuntimeError("未找到目标进程")
    killed = []
    for proc in targets:
        try:
            proc_name = _safe(proc.name, str(proc.pid))
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except psutil.TimeoutExpired:
                proc.kill()
            killed.append(f"{proc_name}({proc.pid})")
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            raise RuntimeError(f"结束进程失败：{exc}") from exc
    return "已结束进程：" + ", ".join(killed)


def restart_service(name: str) -> str:
    if not SERVICE_NAME_RE.match(name or ""):
        raise ValueError("服务名不合法")
    if IS_WINDOWS:
        code1, out1 = _run(["sc.exe", "stop", name], 30)
        time.sleep(2)
        code2, out2 = _run(["sc.exe", "start", name], 30)
        if code2 != 0:
            raise RuntimeError(f"启动服务失败：{out2.strip() or out1.strip()}")
        return f"服务 {name} 已重启"
    if platform.system().lower() == "linux":
        code, out = _run(["systemctl", "restart", name], 30)
        if code != 0:
            raise RuntimeError(f"重启服务失败：{out.strip()}")
        return f"服务 {name} 已重启"
    raise RuntimeError("当前系统不支持服务管理")


def network_info() -> dict[str, Any]:
    result: dict[str, Any] = {"interfaces": [], "listening": [], "connections": 0, "gateway": "", "dns": []}
    if psutil:
        for nic, addrs in psutil.net_if_addrs().items():
            entry = {"name": nic, "addresses": [], "up": False}
            stats = psutil.net_if_stats().get(nic)
            entry["up"] = bool(stats and stats.isup)
            for addr in addrs:
                if addr.family in (socket.AF_INET, socket.AF_INET6):
                    entry["addresses"].append(addr.address)
            result["interfaces"].append(entry)
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.status == psutil.CONN_LISTEN:
                    result["listening"].append(
                        {
                            "port": conn.laddr.port if conn.laddr else None,
                            "address": conn.laddr.ip if conn.laddr else "",
                            "pid": conn.pid,
                        }
                    )
                result["connections"] += 1
        except Exception:  # noqa: BLE001
            pass
        result["listening"] = result["listening"][:100]
    if IS_WINDOWS:
        code, out = _run(["powershell", "-NoProfile", "-Command", "(Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object -First 1).NextHop"], 8)
        if code == 0:
            result["gateway"] = out.strip()
        code, out = _run(["powershell", "-NoProfile", "-Command", "(Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object {$_.ServerAddresses}).ServerAddresses -join ','"], 8)
        if code == 0:
            result["dns"] = [x for x in out.strip().split(",") if x]
    return result


def ping_host(host: str, count: int = 4) -> dict[str, Any]:
    host = (host or "").strip()
    if not re.match(r"^[A-Za-z0-9_.:\-]{1,253}$", host):
        raise ValueError("主机名/IP 不合法")
    count = max(1, min(int(count), 10))
    if IS_WINDOWS:
        args = ["ping", "-n", str(count), host]
    else:
        args = ["ping", "-c", str(count), "-W", "2", host]
    code, out = _run(args, timeout=count * 3 + 5)
    return {"host": host, "ok": code == 0, "output": out[-4000:]}


def _temp_dirs() -> list[Path]:
    dirs = [Path(tempfile.gettempdir())]
    if IS_WINDOWS:
        for env in ("TEMP", "TMP"):
            value = os.environ.get(env)
            if value:
                dirs.append(Path(value))
        windir = os.environ.get("WINDIR", r"C:\Windows")
        dirs.append(Path(windir) / "Temp")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Temp")
    unique: list[Path] = []
    seen = set()
    for path in dirs:
        try:
            resolved = path.resolve()
        except Exception:  # noqa: BLE001
            continue
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def cleanup_temp(older_than_hours: int = 24) -> dict[str, Any]:
    """只清理系统/用户临时目录中超过指定时间的文件，避免误删业务数据。"""
    threshold = time.time() - max(1, older_than_hours) * 3600
    freed = 0
    removed = 0
    failed = 0
    for root in _temp_dirs():
        for path in root.rglob("*"):
            try:
                if path.is_dir() or path.is_symlink():
                    continue
                stat = path.stat()
                if stat.st_mtime > threshold:
                    continue
                size = stat.st_size
                path.unlink()
                freed += size
                removed += 1
            except Exception:  # noqa: BLE001
                failed += 1
    return {
        "freed_bytes": freed,
        "freed_text": human_bytes(freed),
        "removed_files": removed,
        "failed_files": failed,
        "temp_dirs": [str(p) for p in _temp_dirs()],
    }


def collect_inspection() -> dict[str, Any]:
    return {
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "system": system_info(),
        "disks": disk_info(),
        "network": network_info(),
        "top_processes": top_processes(8),
        "services_total": len(list_services()) if IS_WINDOWS else None,
    }
