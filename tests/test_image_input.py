"""[モック／上流照合] 画像入力（/reforge・/upscale）と input_dir の扱い。

調査結果（上流 ImageSmith 固定コミット dcc08071e6e6c8b35aeb971b829e8a3f122790e7）:
- input_dir は WorkflowManager.__init__ でフォルダ作成とログ出力に使うだけ（画像の受け渡しには使わない）
- 添付画像は ComfyUI の POST /api/upload/image へ直接アップロードされ、応答の name が
  image_input_node_id のノードの inputs.image に入る

UpstreamWorkflowManagerTest は上流のコードを読み込んで照合する。環境変数 IMAGESMITH_SRC に
ImageSmith のフォルダ（main.py がある場所）を指定し、その venv の python で実行したときだけ動く。
上流のファイルは変更しない（バイトコードも書かない）。設定・ワークフローは一時フォルダに作る。
"""
import json
import os
import sys
import tempfile
import unittest

from helpers import SAMPLE_API_WORKFLOW
import setup_imagesmith as si

UPSTREAM = os.environ.get("IMAGESMITH_SRC")

IMG2IMG_WORKFLOW = {
    **json.loads(json.dumps(SAMPLE_API_WORKFLOW)),
    "10": {"class_type": "LoadImage", "inputs": {"image": "example.png", "upload": "image"}},
    "12": {"class_type": "VAEEncode", "inputs": {"pixels": ["10", 0], "vae": ["4", 2]}},
}


def wf_spec(**over):
    w = {"name": "my_image", "type": "txt2img", "description": "Image", "file": "C:/x/my_image_api.json",
         "prompt_node_id": "6", "seed_node_id": "3", "default": True,
         "allowed_users": ["me"], "allowed_channels": ["comfyui-generation"]}
    w.update(over)
    return w


def spec(*workflows):
    return {"comfy_url": "http://127.0.0.1:8188", "workflows": list(workflows) or [wf_spec()]}


def load_yaml(text):
    try:
        import yaml
    except ImportError:
        return None
    return yaml.safe_load(text)


class RenderConfigImageInputTest(unittest.TestCase):
    def test_input_dir_stays_relative_input(self):
        text = si.render_config(spec())
        self.assertIn('  input_dir: "input"\n', text)
        cfg = load_yaml(text)
        if cfg is not None:
            self.assertEqual(cfg["comfyui"]["input_dir"], "input")

    def test_image_input_node_id_written_for_img2img_and_upscale(self):
        text = si.render_config(spec(
            wf_spec(),
            wf_spec(name="my_reforge", type="img2img", file="C:/x/img2img_api.json", image_input_node_id="10"),
            wf_spec(name="my_upscale", type="upscale", file="C:/x/upscale_api.json", image_input_node_id="17",
                    seed_node_id=None),
        ))
        self.assertIn('image_input_node_id: "10"', text)
        self.assertIn('image_input_node_id: "17"', text)
        cfg = load_yaml(text)
        if cfg is not None:
            self.assertEqual(cfg["workflows"]["my_reforge"]["image_input_node_id"], "10")
            self.assertEqual(cfg["workflows"]["my_upscale"]["image_input_node_id"], "17")
            self.assertEqual(cfg["workflows"]["my_reforge"]["type"], "img2img")
            # img2img と txt2img の既定はそれぞれ別（種類ごとに1つ）
            self.assertTrue(cfg["workflows"]["my_reforge"].get("default"))
            self.assertTrue(cfg["workflows"]["my_image"].get("default"))

    def test_txt2img_output_unchanged_by_image_support(self):
        """txt2img だけの設定は、画像入力の設定を含まない（従来どおり）。"""
        text = si.render_config(spec())
        self.assertNotIn("image_input_node_id", text)
        self.assertIn('text_prompt_node_id: "6"', text)
        self.assertIn("    type: txt2img\n", text)
        self.assertIn('workflowjson["3"]["inputs"]["seed"]', text)
        self.assertIn('allowed_channels: ["comfyui-generation"]', text)


@unittest.skipUnless(UPSTREAM and os.path.isfile(os.path.join(UPSTREAM, "main.py")),
                     "上流照合は IMAGESMITH_SRC（ImageSmith のフォルダ）を指定し、その venv の python で実行したときだけ")
class UpstreamWorkflowManagerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True  # 上流のフォルダに __pycache__ を作らない
        if UPSTREAM not in sys.path:
            sys.path.insert(0, UPSTREAM)
        try:
            from src.comfy.workflow_manager import WorkflowManager  # noqa: F401
        except ImportError as e:
            raise unittest.SkipTest(f"上流の依存（PyYAML・Pillow・rich）が無い python です: {e}")
        cls.WorkflowManager = WorkflowManager

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        for name, wf in (("t2i.json", SAMPLE_API_WORKFLOW), ("i2i.json", IMG2IMG_WORKFLOW)):
            with open(os.path.join(t, name), "w", encoding="utf-8") as f:
                json.dump(wf, f)
        text = si.render_config(spec(
            wf_spec(file=os.path.join(t, "t2i.json")),
            wf_spec(name="my_reforge", type="img2img", file=os.path.join(t, "i2i.json"), image_input_node_id="10"),
            wf_spec(name="bad_node", type="img2img", file=os.path.join(t, "i2i.json"), image_input_node_id="99",
                    default=False),
            wf_spec(name="no_image_input", type="img2img", file=os.path.join(t, "i2i.json"), image_input_node_id="6",
                    default=False),
        ))
        # render_config は ./workflows/<名前> を書く。上流は作業フォルダ基準で読むため、一時フォルダの絶対パスに置き換える
        text = text.replace('"./workflows/t2i.json"', json.dumps(os.path.join(t, "t2i.json")))
        text = text.replace('"./workflows/i2i.json"', json.dumps(os.path.join(t, "i2i.json")))
        self.cfg_path = os.path.join(t, "configuration.yml")
        with open(self.cfg_path, "w", encoding="ascii") as f:
            f.write(text)
        self.wm = self.WorkflowManager(self.cfg_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_input_dir_only_creates_folder_next_to_config(self):
        self.assertEqual(os.path.normcase(str(self.wm.input_dir)), os.path.normcase(os.path.join(self.tmp.name, "input")))
        self.assertTrue(os.path.isdir(self.wm.input_dir))
        self.assertEqual(os.listdir(self.wm.input_dir), [])

    def test_uploaded_name_goes_to_load_image_node(self):
        uploaded = {"name": "image (1)", "subfolder": "", "type": "input"}  # ComfyUI /upload/image の応答の形
        wf = self.wm.prepare_workflow("my_reforge", prompt="a test", image=uploaded)
        self.assertEqual(wf["10"]["inputs"]["image"], "image (1)")
        self.assertEqual(wf["6"]["inputs"]["text"], "a test")
        self.assertEqual(os.listdir(self.wm.input_dir), [])  # 画像はローカルに保存されない

    def test_invalid_node_id_rejected(self):
        uploaded = {"name": "image", "subfolder": "", "type": "input"}
        with self.assertRaises(ValueError):
            self.wm.prepare_workflow("bad_node", prompt="x", image=uploaded)  # 存在しないノード
        with self.assertRaises(ValueError):
            self.wm.prepare_workflow("no_image_input", prompt="x", image=uploaded)  # image 入力が無いノード

    def test_txt2img_unaffected(self):
        wf = self.wm.prepare_workflow("my_image", prompt="a cat")
        self.assertEqual(wf["6"]["inputs"]["text"], "a cat")
        self.assertIsInstance(wf["3"]["inputs"]["seed"], int)
        self.assertEqual(self.wm.get_default_workflow("txt2img"), "my_image")
        self.assertEqual(self.wm.get_default_workflow("img2img"), "my_reforge")


if __name__ == "__main__":
    unittest.main()
