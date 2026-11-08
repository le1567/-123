#!/usr/bin/env python3
"""生成/查看管理端密钥对（开发与测试用）。

正式使用时，请让手机 APK 在首次启动时生成密钥，公钥粘贴到桌面端绑定；
私钥留在手机 Keystore 中，不要导出。
"""
from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization

from opsdesk.crypto_utils import generate_keypair, public_pem_from_private  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="生成管理端 ECDSA P-256 密钥")
    parser.add_argument("--private", default=str(ROOT.parent / ".secrets" / "admin_private.pem"))
    parser.add_argument("--public", default=str(ROOT.parent / ".secrets" / "admin_public.pem"))
    parser.add_argument("--show", default="", help="查看已有私钥对应的公钥")
    args = parser.parse_args()

    if args.show:
        pem = public_pem_from_private(Path(args.show).read_bytes())
    else:
        private_path = Path(args.private)
        public_path = Path(args.public)
        if private_path.exists():
            print(f"私钥已存在：{private_path}（如需轮换请先备份后删除）")
            pem = public_pem_from_private(private_path.read_bytes())
        else:
            private_pem, pem = generate_keypair()
            private_path.parent.mkdir(parents=True, exist_ok=True)
            private_path.write_bytes(private_pem)
            private_path.chmod(0o600)
            public_path.parent.mkdir(parents=True, exist_ok=True)
            public_path.write_bytes(pem)
            print(f"私钥：{private_path}\n公钥：{public_path}")
    text = pem.decode("utf-8")
    der = serialization.load_pem_public_key(pem).public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    print("\n--- 公钥 PEM（粘贴到桌面端「管理端公钥」）---")
    print(text)
    print("--- 公钥 Base64(DER) ---")
    print(base64.b64encode(der).decode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
