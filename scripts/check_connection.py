"""Phase 4: ComfyUI API・ワークフロー・ImageSmith設定の読み取り検査（何も変更しない）。

サブコマンド:
  api       ComfyUI の API に接続できるか、モデル一覧、待ち行列、外部公開の兆候
            python check_connection.py api --url http://127.0.0.1:8188
  workflow  ワークフローJSONが「API形式」か、プロンプト/seed/出力ノード、参照モデルの有無
            python check_connection.py workflow --file my_workflow_api.json [--url http://127.0.0.1:8188]
  config    ImageSmith の configuration.yml の検査（トークンの値は表示しない）
            <ImageSmithのvenvのpython> check_connection.py config --file configuration.yml [--url ...]
            ※ YAML の読み込みに pyyaml が必要なため、ImageSmith の仮想環境の python で実行する

ComfyUI へは GET のみ送る。/prompt（生成）や /free（メモリ解放）は送らない。
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # Skill フォルダに __pycache__ を作らない

import argparse
import json
import re
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (UnsafeUrlError, emit, looks_secret, setup_stdout,  # noqa: E402
                     validate_comfy_url)

MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".sft")
# 出力ノードの代表例（ImageSmith は images / gifs / audio 等の出力キーを見て送信する）
KNOWN_OUTPUT_HINTS = ("Save", "Preview", "VideoCombine")
PROMPT_KEYS = ("text", "prompt", "positive", "text_g", "text_l")
SEED_KEYS = ("seed", "noise_seed")
VALID_TYPES = ("txt2img", "img2img", "upscale")
TOKEN_PLACEHOLDERS = {"", "YOUR_TOKEN_HERE", "SET_VIA_ENV_DISCORD_TOKEN"}


# --- HTTP（GETのみ） ----------------------------------------------------------

def http_get_json(base: str, path: str, timeout: float = 5.0, opener=urllib.request.urlopen):
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, method="GET")
    with opener(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def listening_addresses(port: int, runner=None) -> list[str]:
    """netstat から、そのポートで待ち受けているローカルアドレスを返す。"""
    runner = runner or (lambda: subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                                               text=True, timeout=15, errors="replace").stdout)
    try:
        out = runner()
    except (OSError, subprocess.SubprocessError):
        return []
    addrs = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0].upper().startswith("TCP") and "LISTEN" in parts[3].upper():
            local = parts[1]
            if local.rsplit(":", 1)[-1] == str(port):
                addrs.append(local.rsplit(":", 1)[0])
    return addrs


def check_api(url: str, allow_lan: bool = False, opener=urllib.request.urlopen, netstat=None) -> dict:
    res = {"ok": False, "problems": [], "warnings": []}
    try:
        base = validate_comfy_url(url, allow_lan=allow_lan)
    except UnsafeUrlError as e:
        res["problems"].append(f"接続先が安全でないため確認しませんでした: {e}")
        return res
    res["url"] = base
    try:
        stats = http_get_json(base, "/system_stats", opener=opener)
    except (urllib.error.URLError, OSError, ValueError) as e:
        res["problems"].append(f"ComfyUI に接続できません（{type(e).__name__}）。ComfyUI が起動しているか、"
                               "ポート番号が合っているか確認してください。")
        return res
    system = stats.get("system", {}) if isinstance(stats, dict) else {}
    res["comfyui_version"] = system.get("comfyui_version")
    res["devices"] = [{"name": d.get("name"), "vram_total_gb": round((d.get("vram_total") or 0) / 1024 ** 3, 1),
                       "vram_free_gb": round((d.get("vram_free") or 0) / 1024 ** 3, 1)}
                      for d in stats.get("devices", []) if isinstance(d, dict)]
    argv = system.get("argv") or []
    if any(str(a).startswith("--listen") for a in argv):
        idx = [i for i, a in enumerate(argv) if str(a).startswith("--listen")][0]
        val = argv[idx].split("=", 1)[1] if "=" in str(argv[idx]) else (
            argv[idx + 1] if idx + 1 < len(argv) and not str(argv[idx + 1]).startswith("--") else "0.0.0.0")
        if val not in ("127.0.0.1", "localhost", "::1"):
            res["warnings"].append(f"ComfyUI が --listen {val} で起動しています。LANや外部から接続できる可能性があります。"
                                   "このSkillは設定を変更しません。意図しない場合は起動設定を見直してください。")
    try:
        q = http_get_json(base, "/queue", opener=opener)
        res["queue"] = {"running": len(q.get("queue_running", [])), "pending": len(q.get("queue_pending", []))}
    except (urllib.error.URLError, OSError, ValueError):
        res["warnings"].append("/queue を取得できませんでした。")
    try:
        folders = http_get_json(base, "/models", opener=opener)
        res["model_folders"] = folders if isinstance(folders, list) else []
    except (urllib.error.URLError, OSError, ValueError):
        res["model_folders"] = []
        res["warnings"].append("/models を取得できませんでした（古いComfyUIの可能性）。")
    counts = {}
    for f in ("checkpoints", "diffusion_models", "unet", "text_encoders", "clip", "vae", "loras"):
        if f in res.get("model_folders", []):
            try:
                counts[f] = len(http_get_json(base, f"/models/{f}", opener=opener))
            except (urllib.error.URLError, OSError, ValueError):
                counts[f] = None
    res["model_counts"] = counts
    port = urllib.parse.urlparse(base).port or 80
    addrs = listening_addresses(port, netstat)
    res["listening_on"] = addrs
    exposed = [a for a in addrs if a.strip("[]") in ("0.0.0.0", "::")]
    if exposed:
        res["warnings"].append(f"ポート{port}がすべてのネットワーク（{', '.join(exposed)}）で待ち受けています。"
                               "ルーターでポート開放していなければ外部からは届きませんが、同じLANからは接続できます。")
    res["ok"] = True
    return res


# --- ワークフロー検査 ---------------------------------------------------------

def load_workflow(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def workflow_format(wf) -> str:
    if isinstance(wf, dict) and "nodes" in wf and "links" in wf:
        return "ui"
    if isinstance(wf, dict) and wf and all(isinstance(v, dict) and "class_type" in v for v in wf.values()):
        return "api"
    return "unknown"


def analyze_workflow(wf: dict) -> dict:
    fmt = workflow_format(wf)
    res = {"format": fmt, "problems": [], "nodes": 0, "prompt_candidates": [], "seed_candidates": [],
           "output_nodes": [], "image_inputs": [], "models": []}
    if fmt == "ui":
        res["problems"].append("これは画面保存用（UI形式）のワークフローです。ComfyUIのメニューから"
                               "「Export (API)」でAPI形式として書き出したJSONを使ってください。")
        return res
    if fmt != "api":
        res["problems"].append("ComfyUIのAPI形式ワークフローとして読み取れません。")
        return res
    res["nodes"] = len(wf)
    for nid, node in wf.items():
        ct = node.get("class_type", "")
        inputs = node.get("inputs", {}) or {}
        title = (node.get("_meta") or {}).get("title")
        for k, v in inputs.items():
            if isinstance(v, str) and k in PROMPT_KEYS:
                res["prompt_candidates"].append({"node_id": nid, "class_type": ct, "input_key": k, "title": title})
            if k in SEED_KEYS and isinstance(v, (int, float)):
                res["seed_candidates"].append({"node_id": nid, "class_type": ct, "input_key": k})
            if isinstance(v, str) and v.lower().endswith(MODEL_EXTS):
                res["models"].append({"node_id": nid, "class_type": ct, "input_key": k, "file": v})
            if k == "image" and isinstance(v, str) and ct.startswith("LoadImage"):
                res["image_inputs"].append({"node_id": nid, "class_type": ct})
        if any(h in ct for h in KNOWN_OUTPUT_HINTS):
            res["output_nodes"].append({"node_id": nid, "class_type": ct})
    if not res["output_nodes"]:
        res["problems"].append("保存・出力ノード（SaveImage等）が見つかりません。Discordに結果を返せない可能性があります。")
    return res


def check_models_on_server(models: list[dict], base: str, opener=urllib.request.urlopen) -> list[dict]:
    """参照モデルが ComfyUI のどこかのモデルフォルダ一覧にあるか（GETのみ）。"""
    try:
        folders = http_get_json(base, "/models", opener=opener)
    except (urllib.error.URLError, OSError, ValueError):
        return [dict(m, found=None) for m in models]
    catalog = set()
    for f in folders:
        try:
            for name in http_get_json(base, f"/models/{urllib.parse.quote(f)}", opener=opener):
                catalog.add(name.replace("\\", "/"))
                catalog.add(os.path.basename(name.replace("\\", "/")))
        except (urllib.error.URLError, OSError, ValueError):
            continue
    out = []
    for m in models:
        n = m["file"].replace("\\", "/")
        out.append(dict(m, found=(n in catalog or os.path.basename(n) in catalog)))
    return out


def check_node_types(wf: dict, base: str, opener=urllib.request.urlopen) -> list[str]:
    """ComfyUI に存在しないノード（カスタムノード未導入など）を返す。"""
    missing = []
    for ct in sorted({n.get("class_type") for n in wf.values() if isinstance(n, dict)}):
        try:
            info = http_get_json(base, f"/object_info/{urllib.parse.quote(ct)}", opener=opener)
            if not info:
                missing.append(ct)
        except urllib.error.HTTPError:
            missing.append(ct)
        except (urllib.error.URLError, OSError, ValueError):
            return []
    return missing


# --- ImageSmith 設定検査 -------------------------------------------------------

def validate_config(cfg: dict, config_dir: str, raw_text: str, comfy_check=None) -> dict:
    res = {"problems": [], "warnings": [], "workflows": []}
    if not isinstance(cfg, dict):
        res["problems"].append("設定ファイルの形式が正しくありません（YAMLの辞書になっていません）。")
        return res
    # 文字コード: ImageSmith は open() にエンコーディングを指定しないため、日本語Windows（cp932）では
    # UTF-8 の日本語が読めない／文字化けする恐れがある
    if any(ord(ch) > 127 for ch in raw_text):
        res["warnings"].append("設定ファイルに日本語などの非ASCII文字があります。日本語Windowsでは読み込みに"
                               "失敗する恐れがあるため、英数字だけで書くことを推奨します。")
    token = ((cfg.get("discord") or {}).get("token")) if isinstance(cfg.get("discord"), dict) else None
    if token is not None and str(token) not in TOKEN_PLACEHOLDERS:
        if looks_secret(str(token)) or len(str(token)) > 40:
            res["warnings"].append("discord.token に実際のトークンらしき値が書かれています（値は表示しません）。"
                                   "環境変数 DISCORD_TOKEN に移し、ファイルには置かないことを推奨します。")
    insts = ((cfg.get("comfyui") or {}).get("instances")) or []
    if not insts:
        res["problems"].append("comfyui.instances がありません。")
    for inst in insts:
        try:
            validate_comfy_url(str(inst.get("url", "")))
        except UnsafeUrlError as e:
            res["problems"].append(f"comfyui.instances の url: {e}")
    workflows = cfg.get("workflows") or {}
    if not workflows:
        res["problems"].append("workflows が1つもありません。")
    defaults = {}
    for name, w in workflows.items():
        item = {"name": name, "problems": [], "warnings": []}
        if not isinstance(w, dict):
            item["problems"].append("設定が辞書形式ではありません。")
            res["workflows"].append(item)
            continue
        if w.get("type") not in VALID_TYPES:
            item["problems"].append(f"type は {', '.join(VALID_TYPES)} のいずれかにしてください（動画も txt2img）。")
        if w.get("default"):
            defaults.setdefault(w.get("type"), []).append(name)
        path = w.get("workflow")
        wf = None
        if not path:
            item["problems"].append("workflow（JSONファイルのパス）がありません。")
        else:
            full = path if os.path.isabs(path) else os.path.normpath(os.path.join(config_dir, path))
            if not os.path.isfile(full):
                item["problems"].append(f"ワークフローファイルが見つかりません: {path}")
            else:
                try:
                    wf = load_workflow(full)
                except (OSError, ValueError):
                    item["problems"].append(f"ワークフローJSONを読み込めません: {path}")
        if wf is not None:
            a = analyze_workflow(wf)
            item["problems"] += a["problems"]
            if a["format"] == "api":
                nid = str(w.get("text_prompt_node_id", ""))
                key = w.get("text_prompt_input_key", "text")
                if not nid:
                    item["warnings"].append("text_prompt_node_id がありません。Discordのプロンプトが反映されません。")
                elif nid not in wf:
                    item["problems"].append(f"text_prompt_node_id {nid} がワークフローにありません。")
                elif key not in (wf[nid].get("inputs") or {}):
                    item["problems"].append(f"ノード {nid} に入力 '{key}' がありません"
                                            "（text_prompt_input_key を確認）。")
                if w.get("type") in ("img2img", "upscale"):
                    iid = str(w.get("image_input_node_id", ""))
                    if not iid or iid not in wf or "image" not in (wf[iid].get("inputs") or {}):
                        item["problems"].append("image_input_node_id が正しくありません（画像入力ノードのID）。")
                item["models"] = [m["file"] for m in a["models"]]
                if comfy_check:
                    item.update(comfy_check(wf, a))
        sec = w.get("security") or {}
        if not sec.get("enabled"):
            item["warnings"].append("security が無効です。サーバーの全員がこのワークフローを使えます。")
        else:
            if not sec.get("allowed_users") and not sec.get("allowed_roles"):
                item["warnings"].append("security は有効ですが、許可ユーザー・ロールが空です（ユーザー制限なし）。")
            if not sec.get("allowed_channels"):
                item["warnings"].append("許可チャンネルが空です（どのチャンネルでも使えます）。")
        res["workflows"].append(item)
    for t, names in defaults.items():
        if len(names) > 1:
            res["warnings"].append(f"type {t} で default: true が複数あります（{', '.join(names)}）。最初の1つだけが使われます。")
    res["ok"] = not res["problems"] and not any(w["problems"] for w in res["workflows"])
    return res


def make_comfy_checker(base: str, opener=urllib.request.urlopen):
    def _check(wf, analysis):
        models = check_models_on_server(analysis["models"], base, opener=opener)
        return {"missing_models": [m["file"] for m in models if m["found"] is False],
                "missing_node_types": check_node_types(wf, base, opener=opener)}
    return _check


# --- 表示 -----------------------------------------------------------------

def human_api(r: dict) -> list[str]:
    lines = ["=== ComfyUI API 接続確認（GETのみ） ==="]
    if r.get("ok"):
        lines.append(f"[OK] {r['url']} に接続できました（ComfyUI {r.get('comfyui_version')}）")
        for d in r.get("devices", []):
            lines.append(f"     GPU: {d['name']}  VRAM空き {d['vram_free_gb']} / {d['vram_total_gb']}GB")
        if "queue" in r:
            lines.append(f"     待ち行列: 実行中 {r['queue']['running']} / 待機 {r['queue']['pending']}")
        if r.get("model_counts"):
            lines.append("     モデル数: " + ", ".join(f"{k} {v}" for k, v in r["model_counts"].items()))
    for p in r["problems"]:
        lines.append(f"[NG] {p}")
    for w in r["warnings"]:
        lines.append(f"[注意] {w}")
    return lines


def human_workflow(r: dict) -> list[str]:
    lines = [f"=== ワークフロー検査: 形式 {r['format']} / ノード数 {r['nodes']} ==="]
    for p in r["problems"]:
        lines.append(f"[NG] {p}")
    for c in r["prompt_candidates"]:
        lines.append(f"プロンプト候補: ノード {c['node_id']} ({c['class_type']}) 入力 '{c['input_key']}'"
                     + (f" タイトル「{c['title']}」" if c.get("title") else ""))
    for c in r["seed_candidates"]:
        lines.append(f"seed候補    : ノード {c['node_id']} ({c['class_type']}) 入力 '{c['input_key']}'")
    for c in r["output_nodes"]:
        lines.append(f"出力ノード  : ノード {c['node_id']} ({c['class_type']})")
    for m in r.get("models_checked", r["models"]):
        mark = {True: "[あり]", False: "[見つからない]", None: "[未確認]"}[m.get("found")] if "found" in m else "-"
        lines.append(f"参照モデル  : {mark} {m['file']}（ノード {m['node_id']}）")
    for t in r.get("missing_node_types", []):
        lines.append(f"[NG] ComfyUIに無いノード: {t}（カスタムノード未導入の可能性）")
    return lines


def human_config(r: dict) -> list[str]:
    lines = ["=== ImageSmith 設定検査（トークンの値は表示しません） ==="]
    for p in r["problems"]:
        lines.append(f"[NG] {p}")
    for w in r["warnings"]:
        lines.append(f"[注意] {w}")
    for w in r["workflows"]:
        status = "OK" if not w["problems"] else "NG"
        lines.append(f"- ワークフロー {w['name']}: {status}")
        for p in w["problems"]:
            lines.append(f"    [NG] {p}")
        for x in w["warnings"]:
            lines.append(f"    [注意] {x}")
        for m in w.get("missing_models", []):
            lines.append(f"    [NG] ComfyUIに見つからないモデル: {m}")
        for t in w.get("missing_node_types", []):
            lines.append(f"    [NG] ComfyUIに無いノード: {t}")
    lines.append("結果: " + ("合格" if r.get("ok") else "要修正"))
    return lines


# --- 登録済みワークフロー名だけを取り出す（明示的な同意が必要） -----------------------

SAFE_WORKFLOW_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CONSENT_FLAG = "--consent-read-config"


class UnsafeExtraction(Exception):
    """安全に抽出できない。メッセージに設定ファイルの中身を含めないこと。"""


def _looks_like_random_secret(name: str) -> bool:
    # 長く、英字と数字が混ざった名前は、トークン・キーを名前にしている可能性があるため扱わない
    return len(name) >= 24 and any(c.isdigit() for c in name) and any(c.isalpha() for c in name)


def extract_workflow_names(raw: bytes) -> list[str]:
    """configuration.yml の workflows 直下のキー名だけを返す。

    YAML 全体は解釈しない（トークン等の値を一切扱わないため）。行単位で
    「トップレベルの workflows: の1段下にあるキー名」だけを見る。少しでも想定外の書き方
    （タブ、フロー形式 {...}、引用符付きキー、アンカー、複数の workflows など）があれば中止する。
    """
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UnsafeExtraction("文字コードを判定できません")
    if "\t" in text:
        raise UnsafeExtraction("タブ文字を含むため安全に解析できません")
    lines = text.splitlines()
    heads = [i for i, l in enumerate(lines) if re.match(r"^workflows\s*:", l)]
    if len(heads) != 1:
        raise UnsafeExtraction("トップレベルの workflows がちょうど1つではありません")
    if not re.match(r"^workflows\s*:\s*(#.*)?$", lines[heads[0]]):
        raise UnsafeExtraction("workflows の書き方が想定外です（1行形式など）")
    names, indent = [], None
    for line in lines[heads[0] + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        lead = len(line) - len(line.lstrip(" "))
        if lead == 0:
            if line.startswith(("---", "...")):
                raise UnsafeExtraction("複数文書の区切りがあります")
            break  # 次のトップレベルのキー
        if indent is None:
            indent = lead
        if lead < indent:
            raise UnsafeExtraction("インデントが不揃いです")
        if lead == indent:
            m = re.match(r"^ +([A-Za-z0-9_-]+)\s*:\s*(#.*)?$", line)
            if not m:
                raise UnsafeExtraction("ワークフロー名の行が想定外の形式です")
            names.append(m.group(1))
        # lead > indent はワークフローの中身。値は読まない
    if not names:
        raise UnsafeExtraction("ワークフローが見つかりません")
    for n in names:
        if not SAFE_WORKFLOW_NAME.match(n) or looks_secret(n) or _looks_like_random_secret(n):
            raise UnsafeExtraction("秘密情報の可能性がある名前を含むため中止しました（名前は表示しません）")
    if len(set(names)) != len(names):
        raise UnsafeExtraction("同じ名前が重複しています")
    return names


def consent_refusal() -> str:
    return ("[未実行] 設定ファイル（configuration.yml）にはトークンなどの秘密情報が含まれる可能性があるため、"
            "通常は開きません。ユーザーが明示的に同意した場合だけ " + CONSENT_FLAG + " を付けて実行してください。")


def main(argv=None) -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("api", "workflow", "config", "workflow-names"):
        p = sub.add_parser(name)
        p.add_argument("--json", action="store_true")
        p.add_argument("--read-only", action="store_true", help="読み取り専用（このスクリプトは常に読み取り専用）")
        p.add_argument("--allow-lan", action="store_true", help="家庭内LANのComfyUIも許可（既定はこのPCのみ）")
        p.add_argument("--url", default=None if name != "api" else "http://127.0.0.1:8188")
        if name != "api":
            p.add_argument("--file", required=True)
        if name in ("config", "workflow-names"):
            p.add_argument(CONSENT_FLAG, dest="consent", action="store_true",
                           help="設定ファイルを開くことにユーザーが明示的に同意した場合だけ付ける")
    a = ap.parse_args(argv)

    if a.cmd in ("config", "workflow-names") and not a.consent:
        print(consent_refusal())
        return 5

    if a.cmd == "workflow-names":
        try:
            with open(a.file, "rb") as f:   # 読み取りのみ。書き込みモードでは開かない
                raw = f.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise UnsafeExtraction("ファイルが大きすぎます")
            names = extract_workflow_names(raw)
        except UnsafeExtraction as e:
            print(f"[中止] 安全に抽出できませんでした（{e}）。設定ファイルの中身は表示していません。")
            return 1
        except Exception:  # noqa: BLE001 — どんな例外でも中身を出さない
            print("[中止] 設定ファイルを読み込めませんでした。設定ファイルの中身は表示していません。")
            return 1
        if a.json:
            print(json.dumps({"workflow_names": names}, ensure_ascii=False))
        else:
            print("=== 登録済みのワークフロー名（名前だけを表示・設定ファイルは変更していません） ===")
            for n in names:
                print(f"- {n}")
        return 0

    if a.cmd == "api":
        r = check_api(a.url, a.allow_lan)
        emit(r, a.json, human_api(r))
        return 0 if r["ok"] else 1

    if a.cmd == "workflow":
        try:
            wf = load_workflow(a.file)
        except (OSError, ValueError) as e:
            print(f"[NG] ワークフローを読み込めません: {type(e).__name__}")
            return 1
        r = analyze_workflow(wf)
        if a.url and r["format"] == "api":
            try:
                base = validate_comfy_url(a.url, a.allow_lan)
                r["models_checked"] = check_models_on_server(r["models"], base)
                r["missing_node_types"] = check_node_types(wf, base)
            except UnsafeUrlError as e:
                r["problems"].append(str(e))
        emit(r, a.json, human_workflow(r))
        bad = r["problems"] or r.get("missing_node_types") or any(
            m.get("found") is False for m in r.get("models_checked", []))
        return 1 if bad else 0

    try:
        import yaml  # ImageSmith の venv には入っている
    except ImportError:
        print("pyyaml がありません。ImageSmith の仮想環境の python（例: .venv\\Scripts\\python.exe）で実行してください。")
        return 2
    try:
        with open(a.file, "rb") as f:
            raw = f.read().decode("utf-8", "replace")
        cfg = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as e:
        # 例外メッセージには設定ファイルの一部（トークン含む可能性）が入るため出力しない
        print(f"[NG] 設定ファイルを読み込めません（{type(e).__name__}）。インデントやコロンの位置を確認してください。")
        return 1
    checker = None
    if a.url:
        try:
            checker = make_comfy_checker(validate_comfy_url(a.url, a.allow_lan))
        except UnsafeUrlError as e:
            print(f"[NG] {e}")
            return 1
    r = validate_config(cfg, os.path.dirname(os.path.abspath(a.file)), raw, checker)
    emit(r, a.json, human_config(r))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
