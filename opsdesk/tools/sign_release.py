#!/usr/bin/env python3
"""生成并签名更新清单 ops/releases/manifest.json。

示例：
    python opsdesk/tools/sign_release.py --version 1.0.2 --file dist/OpsDesk-1.0.2.zip \
        --notes "修复巡检超时" --key .secrets/admin_private.pem
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opsdesk.crypto_utils import sha256_file, sign_b64  # noqa: E402
from opsdesk.util import iso  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="签名 OpsDesk 更新清单")
    parser.add_argument("--version", required=True)
    parser.add_argument("--file", action="append", required=True, help="要发布的文件，可重复")
    parser.add_argument("--notes", default="")
    parser.add_argument("--url-prefix", default="")
    parser.add_argument("--key", default=str(ROOT.parent / ".secrets" / "admin_private.pem"))
    parser.add_argument("--out", default=str(ROOT.parent / "ops" / "releases" / "manifest.json"))
    parser.add_argument("--mandatory", action="store_true")
    parser.add_argument("--channel", default="stable")
    args = parser.parse_args()

    key_path = Path(args.key)
    if not key_path.exists():
        print(f"管理端私钥不存在：{key_path}", file=sys.stderr)
        return 2
    prefix = args.url_prefix or "https://api.github.com/repos/le1567/-123/contents/ops/releases/files"
    files = []
    for item in args.file:
        path = Path(item)
        if not path.exists():
            print(f"文件不存在：{path}", file=sys.stderr)
            return 2
        files.append(
            {
                "path": path.name,
                "url": f"{prefix.rstrip('/')}/{path.name}",
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    doc = {
        "version": args.version,
        "channel": args.channel,
        "released_at": iso(),
        "notes": args.notes,
        "mandatory": bool(args.mandatory),
        "files": files,
    }
    doc["sig"] = sign_b64(key_path.read_bytes(), doc)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"清单已写入：{out}")
    for item in files:
        print(f"  {item['path']}  sha256={item['sha256'][:16]}…  size={item['size']}")
    print("请把文件上传到 ops/releases/files/ 后再发布清单。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
