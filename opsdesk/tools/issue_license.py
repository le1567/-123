#!/usr/bin/env python3
"""签发设备授权文件。

示例：
    python opsdesk/tools/issue_license.py --device OPS-XXXX --customer "某某公司" --days 365
    python opsdesk/tools/issue_license.py --device OPS-XXXX --out /tmp/license.json

默认读取仓库根目录 .secrets/vendor_private.pem，输出到 ops/licenses/<device>.json。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opsdesk.crypto_utils import sign_b64  # noqa: E402
from opsdesk.util import iso  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="签发 OpsDesk 设备授权")
    parser.add_argument("--device", required=True, help="设备码，例如 OPS-1A2B3C4D-...")
    parser.add_argument("--customer", default="", help="客户名称")
    parser.add_argument("--plan", default="enterprise", help="授权套餐标识")
    parser.add_argument("--days", type=int, default=365, help="有效天数")
    parser.add_argument("--features", default="inspect,remote,update,remote_lock", help="功能列表，逗号分隔")
    parser.add_argument("--offline-hours", type=int, default=72, help="离线宽限小时数（最大 72）")
    parser.add_argument("--key", default=str(ROOT.parent / ".secrets" / "vendor_private.pem"), help="厂商私钥路径")
    parser.add_argument("--out", default="", help="输出文件；默认 ops/licenses/<device>.json")
    args = parser.parse_args()

    device = args.device.strip().upper()
    if not device.startswith("OPS-"):
        print("设备码格式不正确，应以 OPS- 开头", file=sys.stderr)
        return 2
    key_path = Path(args.key)
    if not key_path.exists():
        print(f"厂商私钥不存在：{key_path}", file=sys.stderr)
        print("请把部署时生成的 vendor_private.pem 放到该位置（切勿放入仓库）", file=sys.stderr)
        return 2

    now = time.time()
    doc = {
        "license_id": f"LIC-{int(now)}-{device.split('-')[1][:6]}",
        "device_id": device,
        "customer": args.customer,
        "plan": args.plan,
        "features": [x.strip() for x in args.features.split(",") if x.strip()],
        "issued_at": iso(now),
        "expires_at": iso(now + max(1, args.days) * 86400),
        "max_offline_hours": min(72, max(1, args.offline_hours)),
        "issuer": "OpsDesk",
    }
    doc["sig"] = sign_b64(key_path.read_bytes(), doc)

    out = Path(args.out) if args.out else ROOT.parent / "ops" / "licenses" / f"{device}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"授权已生成：{out}")
    print(f"设备码：{device}")
    print(f"有效期至：{doc['expires_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
