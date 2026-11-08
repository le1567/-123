"""核心逻辑单元测试：授权、离线锁定、时钟回拨、指令验签、配置加密。"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from opsdesk.config import Config
from opsdesk.crypto_utils import generate_keypair, sign_b64, verify_b64
from opsdesk.fingerprint import device_id as real_device_id
from opsdesk.license_manager import (
    OFFLINE_LIMIT_SECONDS,
    STATE_ACTIVE,
    STATE_CLOCK_TAMPER,
    STATE_MISSING,
    STATE_OFFLINE_LOCKED,
    LicenseManager,
)
from opsdesk.commands import CommandExecutor, verify_command
from opsdesk.util import iso


class CryptoTest(unittest.TestCase):
    def test_sign_and_verify(self) -> None:
        private_pem, public_pem = generate_keypair()
        doc = {"hello": "世界", "n": 1}
        doc["sig"] = sign_b64(private_pem, doc)
        self.assertTrue(verify_b64(public_pem, doc))
        tampered = dict(doc, n=2)
        self.assertFalse(verify_b64(public_pem, tampered))


class LicenseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OPSDESK_HOME": self.tmp.name})
        self.env.start()
        self.config = Config(Path(self.tmp.name) / "config.json")
        self.vendor_private, self.vendor_public = generate_keypair()
        self.device = "OPS-TEST-0000-0000"
        self.manager = LicenseManager(
            self.config,
            device_id=self.device,
            vendor_pubkey=self.vendor_public,
        )

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _license_doc(self, days: int = 30) -> dict:
        doc = {
            "license_id": "LIC-TEST",
            "device_id": self.device,
            "customer": "测试客户",
            "plan": "enterprise",
            "features": ["inspect", "remote"],
            "issued_at": iso(),
            "expires_at": iso(time.time() + days * 86400),
            "max_offline_hours": 72,
        }
        doc["sig"] = sign_b64(self.vendor_private, doc)
        return doc

    def test_missing_license_locks(self) -> None:
        self.assertEqual(self.manager.state().state, STATE_MISSING)
        self.assertTrue(self.manager.state().locked)

    def test_activate_and_offline_grace(self) -> None:
        ok, message = self.manager.activate(self._license_doc())
        self.assertTrue(ok, message)
        self.assertEqual(self.manager.state().state, STATE_ACTIVE)
        doc = self.manager.store["license"]
        self.assertEqual(doc["device_id"], self.device)

        self.manager.store["last_ok_at"] = time.time() - 2 * 86400
        self.assertEqual(self.manager.state().state, "grace")

        self.manager.store["last_ok_at"] = time.time() - 2.5 * 86400
        self.assertEqual(self.manager.state().state, "grace")

        self.manager.store["last_ok_at"] = time.time() - OFFLINE_LIMIT_SECONDS - 10
        self.assertEqual(self.manager.state().state, STATE_OFFLINE_LOCKED)
        self.assertTrue(self.manager.state().locked)

    def test_wrong_device_rejected(self) -> None:
        doc = self._license_doc()
        doc["device_id"] = "OPS-OTHER"
        doc["sig"] = sign_b64(self.vendor_private, doc)
        ok, message = self.manager.activate(doc)
        self.assertFalse(ok)
        self.assertIn("设备码", message)

    def test_clock_rollback_detected(self) -> None:
        ok, _ = self.manager.activate(self._license_doc())
        self.assertTrue(ok)
        self.manager.store["max_seen_at"] = time.time() + 3600
        self.assertEqual(self.manager.state().state, STATE_CLOCK_TAMPER)

    def test_tampered_store_detected(self) -> None:
        ok, _ = self.manager.activate(self._license_doc())
        self.assertTrue(ok)
        path = self.manager.store_path
        path.write_bytes(b"OPSD1" + b"x" * 40)
        broken = LicenseManager(self.config, device_id=self.device, vendor_pubkey=self.vendor_public)
        self.assertTrue(broken.corrupt)
        self.assertTrue(broken.state().locked)


class ConfigTest(unittest.TestCase):
    def test_token_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"OPSDESK_HOME": tmp}):
                config = Config(Path(tmp) / "config.json")
                config.set_token("ghp_example_token_123456")
                self.assertEqual(config.get_token(), "ghp_example_token_123456")
                raw = json.loads((Path(tmp) / "config.json").read_text(encoding="utf-8"))
                self.assertNotIn("ghp_example_token_123456", json.dumps(raw))

    def test_device_id_stable(self) -> None:
        self.assertEqual(real_device_id(), real_device_id())
        self.assertTrue(real_device_id().startswith("OPS-"))


class CommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OPSDESK_HOME": self.tmp.name})
        self.env.start()
        self.config = Config(Path(self.tmp.name) / "config.json")
        self.vendor_private, self.vendor_public = generate_keypair()
        self.admin_private, self.admin_public = generate_keypair()
        self.device = "OPS-TEST-0000-0000"
        self.license = LicenseManager(self.config, device_id=self.device, vendor_pubkey=self.vendor_public)
        self.executor = CommandExecutor(self.config, self.license)

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _cmd(self, action: str, params: dict | None = None, device: str | None = None) -> dict:
        doc = {
            "cmd_id": f"cmd-{time.time_ns()}",
            "device_id": device or self.device,
            "action": action,
            "params": params or {},
            "issued_at": iso(),
        }
        doc["sig"] = sign_b64(self.admin_private, doc)
        return doc

    def _activate(self) -> None:
        doc = {
            "license_id": "LIC-TEST",
            "device_id": self.device,
            "customer": "测试",
            "plan": "enterprise",
            "features": ["inspect"],
            "issued_at": iso(),
            "expires_at": iso(time.time() + 86400),
        }
        doc["sig"] = sign_b64(self.vendor_private, doc)
        self.assertTrue(self.license.activate(doc)[0])

    def test_reject_bad_signature(self) -> None:
        doc = self._cmd("ping")
        doc["action"] = "sysinfo"
        ok, reason = verify_command(doc, self.admin_public, self.device)
        self.assertFalse(ok)
        self.assertIn("签名", reason)

    def test_reject_wrong_device(self) -> None:
        doc = self._cmd("ping", device="OPS-OTHER")
        ok, reason = verify_command(doc, self.admin_public, self.device)
        self.assertFalse(ok)
        self.assertIn("不匹配", reason)

    def test_locked_rejects_dangerous_command(self) -> None:
        doc = self._cmd("disk_cleanup")
        result = self.executor.execute(doc)
        self.assertFalse(result["ok"])
        self.assertIn("锁定", result["output"])

    def test_unlock_allowed_when_locked(self) -> None:
        self._activate()
        self.license.set_remote_lock(True, "test")
        result = self.executor.execute(self._cmd("unlock"))
        self.assertTrue(result["ok"])
        self.assertFalse(self.license.store["remote_locked"])

    def test_ping(self) -> None:
        self._activate()
        result = self.executor.execute(self._cmd("ping"))
        self.assertTrue(result["ok"])
        self.assertIn("pong", result["output"])


if __name__ == "__main__":
    unittest.main()
