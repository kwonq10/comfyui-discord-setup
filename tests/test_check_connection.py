"""[モック] ComfyUI API接続確認・ワークフロー検査・ImageSmith設定検査。
モックComfyUIは GET 以外を受けると失敗する（生成やメモリ解放を送らないことの確認）。"""
import json
import os
import tempfile
import unittest

from helpers import FAKE_TOKEN, SAMPLE_API_WORKFLOW, SAMPLE_UI_WORKFLOW, FakeComfy
import check_connection as cc

NETSTAT_LOCAL = "  TCP    127.0.0.1:8188    0.0.0.0:0    LISTENING    1234\n"
NETSTAT_ALL = "  TCP    0.0.0.0:8188      0.0.0.0:0    LISTENING    1234\n  TCP    [::]:8188  [::]:0  LISTENING  1234\n"


class ApiTest(unittest.TestCase):
    def test_ok(self):
        fake = FakeComfy()
        r = cc.check_api("http://127.0.0.1:8188", opener=fake, netstat=lambda: NETSTAT_LOCAL)
        self.assertTrue(r["ok"])
        self.assertEqual(r["comfyui_version"], "0.42.5")
        self.assertEqual(r["queue"], {"running": 0, "pending": 0})
        self.assertEqual(r["model_counts"]["checkpoints"], 2)
        self.assertEqual(r["warnings"], [])
        self.assertTrue(all(m == "GET" for m, _ in fake.calls))
        self.assertFalse(any("/prompt" in u or "/free" in u for _, u in fake.calls))

    def test_down(self):
        r = cc.check_api("http://127.0.0.1:8188", opener=FakeComfy(down=True), netstat=lambda: "")
        self.assertFalse(r["ok"])
        self.assertIn("接続できません", r["problems"][0])

    def test_public_url_never_contacted(self):
        fake = FakeComfy()
        r = cc.check_api("http://203.0.113.5:8188", opener=fake, netstat=lambda: "")
        self.assertFalse(r["ok"])
        self.assertEqual(fake.calls, [])

    def test_exposure_warnings(self):
        stats = FakeComfy().routes["/system_stats"]
        stats = json.loads(json.dumps(stats))
        stats["system"]["argv"] = ["main.py", "--listen", "0.0.0.0"]
        r = cc.check_api("http://127.0.0.1:8188", opener=FakeComfy({"/system_stats": stats}),
                         netstat=lambda: NETSTAT_ALL)
        self.assertEqual(len(r["warnings"]), 2)
        self.assertIn("0.0.0.0", r["listening_on"])

    def test_netstat_parse(self):
        self.assertEqual(cc.listening_addresses(8188, lambda: NETSTAT_LOCAL), ["127.0.0.1"])
        self.assertEqual(cc.listening_addresses(8000, lambda: NETSTAT_LOCAL), [])


class WorkflowTest(unittest.TestCase):
    def test_api_format_analysis(self):
        a = cc.analyze_workflow(SAMPLE_API_WORKFLOW)
        self.assertEqual(a["format"], "api")
        self.assertEqual(a["problems"], [])
        self.assertEqual({c["node_id"] for c in a["prompt_candidates"]}, {"6", "7"})
        self.assertEqual(a["seed_candidates"][0]["node_id"], "3")
        self.assertEqual(a["output_nodes"][0]["class_type"], "SaveImage")
        self.assertEqual(a["models"][0]["file"], "sd_xl_base_1.0.safetensors")

    def test_ui_format_rejected(self):
        a = cc.analyze_workflow(SAMPLE_UI_WORKFLOW)
        self.assertEqual(a["format"], "ui")
        self.assertIn("Export (API)", a["problems"][0])

    def test_no_output_node(self):
        wf = {k: v for k, v in SAMPLE_API_WORKFLOW.items() if k != "9"}
        self.assertTrue(cc.analyze_workflow(wf)["problems"])

    def test_models_checked_against_server(self):
        models = [{"node_id": "4", "file": "sd_xl_base_1.0.safetensors"}, {"node_id": "4", "file": "other.safetensors"},
                  {"node_id": "4", "file": "nope.safetensors"}]
        r = cc.check_models_on_server(models, "http://127.0.0.1:8188", opener=FakeComfy())
        self.assertEqual([m["found"] for m in r], [True, True, False])

    def test_missing_node_types(self):
        wf = dict(SAMPLE_API_WORKFLOW, x={"class_type": "VHS_VideoCombine", "inputs": {}})
        self.assertEqual(cc.check_node_types(wf, "http://127.0.0.1:8188", opener=FakeComfy()), ["VHS_VideoCombine"])


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.tmp.name, "workflows"))
        with open(os.path.join(self.tmp.name, "workflows", "img.json"), "w") as f:
            json.dump(SAMPLE_API_WORKFLOW, f)

    def tearDown(self):
        self.tmp.cleanup()

    def cfg(self, **wf_overrides):
        wf = {"type": "txt2img", "workflow": "./workflows/img.json", "text_prompt_node_id": "6",
              "security": {"enabled": True, "allowed_users": ["me"], "allowed_channels": ["gen"]}}
        wf.update(wf_overrides)
        return {"discord": {"token": "SET_VIA_ENV_DISCORD_TOKEN"},
                "comfyui": {"instances": [{"url": "http://127.0.0.1:8188"}]}, "workflows": {"my_image": wf}}

    def test_good(self):
        r = cc.validate_config(self.cfg(), self.tmp.name, "ascii only")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["warnings"], [])

    def test_real_token_in_file_warns_without_leaking(self):
        c = self.cfg()
        c["discord"]["token"] = FAKE_TOKEN
        r = cc.validate_config(c, self.tmp.name, "x")
        text = json.dumps(r, ensure_ascii=False) + "\n".join(cc.human_config(r))
        self.assertIn("トークンらしき値", text)
        self.assertNotIn(FAKE_TOKEN, text)

    def test_non_ascii_warning(self):
        r = cc.validate_config(self.cfg(), self.tmp.name, "description: 画像生成")
        self.assertTrue(any("非ASCII" in w for w in r["warnings"]))

    def test_wrong_prompt_node_and_key(self):
        r = cc.validate_config(self.cfg(text_prompt_node_id="99"), self.tmp.name, "")
        self.assertFalse(r["ok"])
        r = cc.validate_config(self.cfg(text_prompt_input_key="prompt"), self.tmp.name, "")
        self.assertFalse(r["ok"])

    def test_missing_file_and_bad_type(self):
        r = cc.validate_config(self.cfg(workflow="./workflows/none.json", type="video"), self.tmp.name, "")
        self.assertEqual(len(r["workflows"][0]["problems"]), 2)

    def test_security_disabled_warns(self):
        r = cc.validate_config(self.cfg(security={"enabled": False}), self.tmp.name, "")
        self.assertTrue(any("全員" in w for w in r["workflows"][0]["warnings"]))

    def test_public_comfy_url_is_problem(self):
        c = self.cfg()
        c["comfyui"]["instances"][0]["url"] = "https://comfy.example.com"
        self.assertFalse(cc.validate_config(c, self.tmp.name, "")["ok"])

    def test_with_server_check(self):
        checker = cc.make_comfy_checker("http://127.0.0.1:8188", opener=FakeComfy())
        r = cc.validate_config(self.cfg(), self.tmp.name, "", checker)
        self.assertEqual(r["workflows"][0]["missing_models"], [])
        self.assertEqual(r["workflows"][0]["missing_node_types"], [])


if __name__ == "__main__":
    unittest.main()
