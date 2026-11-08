"""密码学工具：ECDSA P-256 签名/验签、AES-GCM 本地加密、密钥生成。

授权文件、远程指令、更新清单均使用 ECDSA(SHA-256) 签名。
私钥永远不进入桌面客户端；客户端只内置/绑定公钥。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class CryptoError(RuntimeError):
    pass


def canonical_bytes(obj: Any) -> bytes:
    """稳定序列化：字段排序、紧凑分隔符、UTF-8，用于签名。"""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def payload_without_sig(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "sig"}


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def generate_keypair() -> tuple[bytes, bytes]:
    """生成 P-256 私钥/公钥 PEM。"""
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def sign_b64(private_pem: bytes, doc: dict) -> str:
    private_key = serialization.load_pem_private_key(private_pem, password=None)
    signature = private_key.sign(canonical_bytes(payload_without_sig(doc)), ec.ECDSA(hashes.SHA256()))
    return b64e(signature)


def verify_b64(public_pem: bytes | str, doc: dict, sig_b64: str | None = None) -> bool:
    if not sig_b64:
        sig_b64 = doc.get("sig") if isinstance(doc, dict) else None
    if not sig_b64:
        return False
    try:
        key_bytes = public_pem.encode("utf-8") if isinstance(public_pem, str) else public_pem
        public_key = serialization.load_pem_public_key(key_bytes)
        public_key.verify(
            b64d(sig_b64), canonical_bytes(payload_without_sig(doc)), ec.ECDSA(hashes.SHA256())
        )
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: os.PathLike | str, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def derive_key(secret: str, salt: bytes, iterations: int = 200_000) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations
    )
    return kdf.derive(secret.encode("utf-8"))


def aes_encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return b"OPSD1" + nonce + ciphertext


def aes_decrypt(key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    if not blob.startswith(b"OPSD1"):
        raise CryptoError("本地数据格式不正确")
    nonce, ciphertext = blob[5:17], blob[17:]
    return AESGCM(key).decrypt(nonce, ciphertext, aad)


def public_pem_from_private(private_pem: bytes) -> bytes:
    private_key = serialization.load_pem_private_key(private_pem, password=None)
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def normalize_public_pem(text: str | bytes) -> bytes:
    """接受 PEM 或 base64(DER) 公钥，统一返回 PEM。"""
    raw = text.encode("utf-8") if isinstance(text, str) else text
    raw = raw.strip()
    if b"-----BEGIN" in raw:
        serialization.load_pem_public_key(raw)  # 校验格式
        return raw + (b"" if raw.endswith(b"\n") else b"\n")
    der = base64.b64decode(raw)
    key = serialization.load_der_public_key(der)
    return key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def sig_prefix(sig: str, length: int = 16) -> str:
    return (sig or "")[:length]
