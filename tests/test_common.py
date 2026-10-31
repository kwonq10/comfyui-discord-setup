"""[モック] 秘密情報マスク・接続先URL検証・パス匿名化。"""
import os
import unittest

from helpers import FAKE_TOKEN  # noqa: F401  (sys.path 設定)
import _common as c


class RedactTest(unittest.TestCase):
    def test_discord_token_masked(self):
        out = c.redact(f"token is {FAKE_TOKEN} end")
        self.assertNotIn(FAKE_TOKEN, out)
        self.assertIn(c.REDACTED, out)

    def test_key_value_masked(self):
        for s in ["DISCORD_TOKEN=abcd1234efgh", "token: 'abcd1234efgh'", '"api_key": "sk-abcdef123456"',
                  "password=hunter22", "sessionKey: 0123456789abcdef"]:
            out = c.redact(s)
            self.assertIn(c.REDACTED, out, s)
            self.assertNotIn("abcd1234efgh", out)

    def test_bearer_and_url_credentials(self):
        self.assertNotIn("abc.def.ghi123", c.redact("Authorization: Bearer abc.def.ghi123"))
        self.assertNotIn("pw", c.redact("http://user:pw@127.0.0.1:8188/"))

    def test_webhook_token_masked(self):
        url = "https://discord.com/api/v10/webhooks/123456789012345678/" + "a" * 60 + "/messages/@original"
        self.assertNotIn("a" * 60, c.redact(url))

    def test_plain_text_untouched(self):
        s = "Registered 4 Discord commands / Bot is ready! / http://127.0.0.1:8188"
        self.assertEqual(c.redact(s), s)
        self.assertFalse(c.looks_secret("my_image"))
        self.assertTrue(c.looks_secret(FAKE_TOKEN))


class UrlTest(unittest.TestCase):
    def test_loopback_allowed(self):
        self.assertEqual(c.validate_comfy_url("http://127.0.0.1:8188/"), "http://127.0.0.1:8188")
        self.assertEqual(c.validate_comfy_url("http://localhost:8000"), "http://localhost:8000")

    def test_lan_requires_flag(self):
        with self.assertRaises(c.UnsafeUrlError):
            c.validate_comfy_url("http://192.168.1.10:8188")
        self.assertEqual(c.validate_comfy_url("http://192.168.1.10:8188", allow_lan=True), "http://192.168.1.10:8188")

    def test_public_and_hostnames_rejected_even_with_lan(self):
        for u in ["http://8.8.8.8:8188", "https://comfy.example.com", "http://0.0.0.0:8188",
                  "ftp://127.0.0.1", "http://user:pw@127.0.0.1:8188", "127.0.0.1:8188"]:
            with self.assertRaises(c.UnsafeUrlError, msg=u):
                c.validate_comfy_url(u, allow_lan=True)


class PathTest(unittest.TestCase):
    def test_anonymize_home(self):
        home = os.path.expanduser("~")
        self.assertEqual(c.anonymize_path(os.path.join(home, "x", "y")), os.path.join("%USERPROFILE%", "x", "y"))
        self.assertEqual(c.anonymize_path(r"D:\tools"), r"D:\tools")


if __name__ == "__main__":
    unittest.main()
