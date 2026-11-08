"""通用工具：时间、JSON、日志、异步线程。"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger("opsdesk")


def now_ts() -> float:
    return time.time()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts is not None else utc_now()
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> float | None:
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return None


def human_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}天{hours:02d}小时{minutes:02d}分"
    if hours:
        return f"{hours}小时{minutes:02d}分"
    if minutes:
        return f"{minutes}分{secs:02d}秒"
    return f"{secs}秒"


def human_bytes(num: float) -> str:
    num = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0 or unit == "TB":
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} TB"


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    if LOG.handlers:
        return LOG
    LOG.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.handlers.RotatingFileHandler(
        log_dir / "opsdesk.log", maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    LOG.addHandler(fh)
    LOG.addHandler(sh)
    return LOG


def run_async(fn: Callable[[], Any], on_done: Callable[[Any, Exception | None], None] | None = None) -> threading.Thread:
    """在后台线程执行 fn，回调在主线程外触发，由调用方负责切回 UI 线程。"""

    def _target() -> None:
        result, error = None, None
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001
            error = exc
            LOG.exception("后台任务失败")
        if on_done:
            try:
                on_done(result, error)
            except Exception:  # noqa: BLE001
                LOG.exception("后台任务回调失败")

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    return thread
