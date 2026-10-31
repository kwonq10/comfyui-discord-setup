"""テスト共通: scripts をインポート可能にし、ComfyUI のモック HTTP 応答を作る。"""
import io
import json
import os
import sys
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

# 実在しない形式だけ合うダミー（Discordトークンの形: 24文字.6文字.27文字以上）
# ソースにはトークン形式の文字列を置かない（秘密情報の検査で誤検知されないよう、実行時に組み立てる）
FAKE_TOKEN = ".".join(["MTIzNDU2Nzg5MDEyMzQ1Njc4", "GabCdE", "abcdefghijklmnopqrstuvwxyz0123456789AB"])


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeComfy:
    """GET のみ受け付けるモック ComfyUI。POST が来たらテスト失敗にする。"""

    def __init__(self, routes=None, down=False):
        self.down = down
        self.calls = []
        self.routes = {
            "/system_stats": {"system": {"comfyui_version": "0.42.5", "python_version": "3.13.12 (x)", "argv": ["main.py"]},
                              "devices": [{"name": "cuda:0 NVIDIA GeForce RTX 3060", "vram_total": 12 * 1024 ** 3,
                                           "vram_free": 10 * 1024 ** 3}]},
            "/queue": {"queue_running": [], "queue_pending": []},
            "/models": ["checkpoints", "vae", "loras"],
            "/models/checkpoints": ["sd_xl_base_1.0.safetensors", "sub/other.safetensors"],
            "/models/vae": ["sdxl_vae.safetensors"],
            "/models/loras": [],
            "/object_info/CheckpointLoaderSimple": {"CheckpointLoaderSimple": {}},
            "/object_info/CLIPTextEncode": {"CLIPTextEncode": {}},
            "/object_info/KSampler": {"KSampler": {}},
            "/object_info/SaveImage": {"SaveImage": {}},
            "/object_info/EmptyLatentImage": {"EmptyLatentImage": {}},
            "/object_info/VAEDecode": {"VAEDecode": {}},
        }
        if routes:
            self.routes.update(routes)

    def __call__(self, req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        method = "GET" if isinstance(req, str) else req.get_method()
        self.calls.append((method, url))
        assert method == "GET", f"GET以外が送信された: {method} {url}"
        if self.down:
            raise urllib.error.URLError("Connection refused")
        path = "/" + url.split("/", 3)[3] if url.count("/") >= 3 else "/"
        path = urllib.parse.unquote(path.split("?")[0])
        if path not in self.routes:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return FakeResponse(json.dumps(self.routes[path]).encode())


import urllib.parse  # noqa: E402

SAMPLE_API_WORKFLOW = {
    "3": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 20, "model": ["4", 0]}},
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat", "clip": ["4", 1]}, "_meta": {"title": "Positive"}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}, "_meta": {"title": "Negative"}},
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x", "images": ["8", 0]}},
}

SAMPLE_UI_WORKFLOW = {"last_node_id": 9, "nodes": [{"id": 1}], "links": []}
