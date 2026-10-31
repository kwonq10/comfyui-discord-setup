"""[実機・読み取りのみ] 実際の PC / 起動中の ComfyUI に対する確認。

既定ではスキップする。実行するときだけ環境変数 COMFY_SKILL_LIVE=1 を付ける:
    set COMFY_SKILL_LIVE=1 && python -m unittest discover -s tests -t tests

- ComfyUI へは GET のみ（/system_stats, /queue, /models）。生成・メモリ解放は送らない。
- DISCORD_TOKEN の有無確認は行わない（--no-secret-check 相当）。
- Discord には接続しない。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import helpers  # noqa: E402,F401
import check_connection as cc  # noqa: E402
import check_environment as ce  # noqa: E402
import detect_comfyui as dc  # noqa: E402

LIVE = os.environ.get("COMFY_SKILL_LIVE") == "1"
URL = os.environ.get("COMFY_SKILL_URL", "http://127.0.0.1:8188")


@unittest.skipUnless(LIVE, "実機テストは COMFY_SKILL_LIVE=1 のときだけ")
class LiveReadOnlyTest(unittest.TestCase):
    def test_environment(self):
        r = ce.collect(secret_check=False)
        self.assertEqual(r["windows"]["os"], "Windows")
        self.assertEqual(r["discord_token"], {"checked": False})

    def test_detect(self):
        r = dc.collect([8188, 8000], None, False)
        self.assertIsInstance(r["installations"], list)

    def test_target_dir_reuse_only_target(self):
        """COMFY_SKILL_TARGET（既存の ImageSmith の場所。未指定ならスキップ）だけを調べ、再利用判定になること。"""
        import detect_imagesmith as di
        target = os.environ.get("COMFY_SKILL_TARGET")
        if not target:
            self.skipTest("COMFY_SKILL_TARGET が未指定")
        if not os.path.isdir(target):
            self.skipTest("対象の ImageSmith がこのPCに無い")
        res = di.inspect_target(target)
        self.assertEqual(res["recommendation"]["action"], "reuse")
        self.assertEqual(len(res["installations"]), 1)

    def test_shortcuts_resolve(self):
        """デスクトップのショートカットを読み取り（実行しない）、判定結果の形式が正しいこと。"""
        import check_shortcuts as cs
        for r in cs.check():
            self.assertIn(r["state"], ("判定済み", "不明", "不明（複数の環境を参照）"))
            if r["state"] == "判定済み":
                self.assertTrue(os.path.isdir(r["env"]))

    def test_api_if_running(self):
        r = cc.check_api(URL)
        if not r["ok"]:
            self.skipTest("ComfyUI が起動していない")
        self.assertTrue(r["comfyui_version"])


if __name__ == "__main__":
    unittest.main()
