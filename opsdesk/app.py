"""OpsDesk 桌面运维终端入口。

用法：
    python app.py                   启动图形界面
    python app.py --device-id       打印本机设备码（用于签发授权）
    python app.py --license-state    打印当前授权状态
"""
from __future__ import annotations

import argparse
import platform
import sys


def _enable_dpi_awareness() -> None:
    if platform.system().lower() != "windows":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="OpsDesk 桌面运维终端")
    parser.add_argument("--device-id", action="store_true", help="打印设备码")
    parser.add_argument("--license-state", action="store_true", help="打印授权状态")
    parser.add_argument("--sync", action="store_true", help="执行一次授权联网校验")
    args = parser.parse_args()

    from opsdesk.config import APP_VERSION, Config
    from opsdesk.license_manager import LicenseManager

    if args.device_id:
        print(Config().device_id())
        return 0

    if args.license_state or args.sync:
        config = Config()
        manager = LicenseManager(config)
        if args.sync:
            manager.online_sync()
        status = manager.state()
        print(f"state={status.state}")
        print(status.summary())
        print(f"device_id={manager.device_id}")
        if manager.last_error:
            print(f"error={manager.last_error}")
        return 0 if not status.locked else 2

    _enable_dpi_awareness()
    from opsdesk.ui import OpsDeskApp

    app = OpsDeskApp()
    app.mainloop()
    _ = APP_VERSION
    return 0


if __name__ == "__main__":
    sys.exit(main())
