"""[モック] ComfyUI 検出。一時フォルダに偽の Desktop 設定・ComfyUI フォルダを作って確認。"""
import json
import os
import tempfile
import unittest
from unittest import mock

from helpers import FakeComfy
import detect_comfyui as dc


class DesktopConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = os.path.join(self.tmp.name, "Comfy Desktop")
        os.makedirs(os.path.join(base, "comfy-procs"))
        with open(os.path.join(base, "installations.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "inst-1", "name": "ComfyUI", "installPath": r"D:\ComfyUI", "version": "0.42.5",
                        "launchArgs": "--listen 0.0.0.0 --port 8188", "downloadUrl": "https://example.invalid/secret?x=1"}], f)
        with open(os.path.join(base, "comfy-procs", "inst-1.json"), "w", encoding="utf-8") as f:
            json.dump({"installationId": "inst-1", "port": 8188, "sessionKey": "SESSION-SECRET-VALUE-123"}, f)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_only_whitelisted_keys(self):
        found = dc.detect_comfy_desktop(self.tmp.name)
        self.assertEqual(len(found), 1)
        item = found[0]
        self.assertEqual(item["installPath"], r"D:\ComfyUI")
        self.assertEqual(item["last_known_port"], 8188)
        dumped = json.dumps(found)
        self.assertNotIn("SESSION-SECRET", dumped)
        self.assertNotIn("downloadUrl", dumped)

    def test_listen_warning(self):
        with mock.patch.dict(os.environ, {"APPDATA": self.tmp.name}):
            r = dc.collect([8188], None, False, opener=FakeComfy(down=True))
        self.assertTrue(any("--listen 0.0.0.0" in w for w in r["warnings"]))
        self.assertEqual(r["running"], [])

    def test_missing_config_is_empty(self):
        self.assertEqual(dc.detect_comfy_desktop(os.path.join(self.tmp.name, "nope")), [])
        self.assertEqual(dc.detect_legacy_desktop(self.tmp.name), [])


class ListenFlagsTest(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(dc.listen_flags("--listen"), {"listen": "0.0.0.0", "port": None})
        self.assertEqual(dc.listen_flags("--listen=127.0.0.1 --port=8000"), {"listen": "127.0.0.1", "port": 8000})
        self.assertEqual(dc.listen_flags(""), {"listen": None, "port": None})


class FolderScanTest(unittest.TestCase):
    def test_finds_comfyui_folder(self):
        with tempfile.TemporaryDirectory() as t:
            comfy = os.path.join(t, "ComfyUI_windows_portable", "ComfyUI")
            os.makedirs(os.path.join(comfy, "comfy"))
            os.makedirs(os.path.join(t, "ComfyUI_windows_portable", "python_embeded"))
            for fn in ("main.py", "nodes.py"):
                open(os.path.join(comfy, fn), "w").close()
            os.makedirs(os.path.join(t, "comfy-not-really"))
            found = dc.scan_folders([t])
            self.assertEqual([f["kind"] for f in found], ["portable"])
            self.assertTrue(found[0]["installPath"].endswith("ComfyUI"))


class ProbeTest(unittest.TestCase):
    def test_running(self):
        fake = FakeComfy()
        p = dc.probe(8188, opener=fake)
        self.assertTrue(p["running"])
        self.assertEqual(p["comfyui_version"], "0.42.5")
        self.assertTrue(all(m == "GET" for m, _ in fake.calls))
        self.assertTrue(all("127.0.0.1" in u for _, u in fake.calls))

    def test_not_running(self):
        p = dc.probe(8188, opener=FakeComfy(down=True))
        self.assertFalse(p["running"])


if __name__ == "__main__":
    unittest.main()
