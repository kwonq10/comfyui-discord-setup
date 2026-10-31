"""Phase 1: ComfyUI のインストール場所と起動状態を読み取りで検出する（何も変更しない）。

検出方法:
  1. Comfy Desktop（新しいデスクトップ版）: %APPDATA%\\Comfy Desktop\\installations.json と
     comfy-procs\\*.json から、インストール先・バージョン・起動ポートを読む。
     sessionKey などの値は読み取っても出力しない（許可したキーのみ取り出す）。
  2. 旧 ComfyUI Desktop: %APPDATA%\\ComfyUI\\config.json の basePath（この形式は実機未検証）
  3. ポータブル版・git版: よくある場所で ComfyUI\\main.py と comfy\\ フォルダを探す
  4. 起動状態: 127.0.0.1 の候補ポートへ GET /system_stats（読み取り専用API）

使い方:
    python detect_comfyui.py [--json] [--port 8188 --port 8000] [--search-root D:\\]
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # Skill フォルダに __pycache__ を作らない

import argparse
import glob
import json
import os
import shlex
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import anonymize_path, emit, setup_stdout  # noqa: E402

DEFAULT_PORTS = [8188, 8000]  # 8188: ComfyUI本体の既定 / 8000: Desktop版で使われることがある
INSTALL_KEYS = ("name", "installPath", "version", "comfyVersionTag", "pythonVersion", "status", "variant", "sourceId")
PROC_KEYS = ("installationId", "installPath", "port")


def _load_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def listen_flags(launch_args: str) -> dict:
    """起動引数から --listen / --port だけを取り出す（外部公開の検出用）。"""
    try:
        tokens = shlex.split(launch_args or "", posix=False)
    except ValueError:
        tokens = (launch_args or "").split()
    res = {"listen": None, "port": None}
    for i, t in enumerate(tokens):
        if t.startswith("--listen"):
            val = t.split("=", 1)[1] if "=" in t else (
                tokens[i + 1] if i + 1 < len(tokens) and not tokens[i + 1].startswith("--") else "0.0.0.0")
            res["listen"] = val
        if t.startswith("--port"):
            val = t.split("=", 1)[1] if "=" in t else (tokens[i + 1] if i + 1 < len(tokens) else None)
            try:
                res["port"] = int(val)
            except (TypeError, ValueError):
                pass
    return res


def detect_comfy_desktop(appdata: str) -> list[dict]:
    base = os.path.join(appdata, "Comfy Desktop")
    data = _load_json(os.path.join(base, "installations.json"))
    found = []
    if isinstance(data, list):
        ports = {}
        for p in glob.glob(os.path.join(base, "comfy-procs", "*.json")):
            d = _load_json(p)
            if isinstance(d, dict):
                safe = {k: d.get(k) for k in PROC_KEYS}
                if safe.get("installationId"):
                    ports[safe["installationId"]] = safe.get("port")
        for inst in data:
            if not isinstance(inst, dict):
                continue
            item = {k: inst.get(k) for k in INSTALL_KEYS}
            # Comfy Cloud（sourceId=cloud）はこのPCで動く ComfyUI ではないので対象外
            item["kind"] = "comfy_cloud" if inst.get("sourceId") == "cloud" or not inst.get("installPath") else "comfy_desktop"
            item["last_known_port"] = ports.get(inst.get("id"))
            item["launch_flags"] = listen_flags(inst.get("launchArgs") or "")
            found.append(item)
    return found


def detect_legacy_desktop(appdata: str) -> list[dict]:
    d = _load_json(os.path.join(appdata, "ComfyUI", "config.json"))
    if isinstance(d, dict) and isinstance(d.get("basePath"), str):
        return [{"kind": "legacy_desktop", "installPath": d["basePath"], "note": "旧Desktop形式（実機未検証）"}]
    return []


def is_comfyui_dir(path: str) -> bool:
    return (os.path.isfile(os.path.join(path, "main.py"))
            and os.path.isdir(os.path.join(path, "comfy"))
            and os.path.isfile(os.path.join(path, "nodes.py")))


def default_search_roots() -> list[str]:
    home = os.path.expanduser("~")
    roots = [home, os.path.join(home, "Desktop"), os.path.join(home, "Downloads"),
             os.path.join(home, "Documents")]
    for letter in "CDEFG":
        if os.path.exists(f"{letter}:\\"):
            roots.append(f"{letter}:\\")
    return roots


def scan_folders(roots: list[str], max_depth: int = 3) -> list[dict]:
    """浅い深さだけを探す（全ドライブ総なめはしない）。"""
    found, seen = [], set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        stack = [(root, 0)]
        while stack:
            cur, depth = stack.pop()
            try:
                entries = list(os.scandir(cur))
            except OSError:
                continue
            for e in entries:
                if not e.is_dir(follow_symlinks=False):
                    continue
                name = e.name.lower()
                if name.startswith(".") or name in ("windows", "program files", "program files (x86)",
                                                     "$recycle.bin", "appdata", "node_modules", ".venv", "venv"):
                    continue
                if "comfy" in name and is_comfyui_dir(e.path):
                    key = os.path.normcase(os.path.abspath(e.path))
                    if key not in seen:
                        seen.add(key)
                        kind = "portable" if os.path.isdir(os.path.join(os.path.dirname(e.path), "python_embeded")) else "folder"
                        found.append({"kind": kind, "installPath": e.path})
                    continue
                if depth + 1 < max_depth:
                    stack.append((e.path, depth + 1))
    return found


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.realpath(path))) if path else ""


def dedupe(installs: list[dict]) -> list[dict]:
    """同じインストール先の重複をまとめる。

    Desktop版の installPath（例 D:/X/ComfyUI）と、フォルダ検索で見つかる本体
    （D:/X/ComfyUI/ComfyUI）は同じもの。設定由来の情報を優先して1件にする。
    """
    out, keys = [], {}
    for i in installs:
        path = i.get("installPath") or ""
        k = _key(path)
        if i["kind"] in ("portable", "folder"):
            parent = _key(os.path.dirname(path))
            match = next((o for o in out if o["kind"] in ("comfy_desktop", "legacy_desktop")
                          and _key(o.get("installPath") or "") in (k, parent)), None)
            if match is not None:
                match.setdefault("comfyui_dir", path)
                continue
        if k and k in keys:
            continue
        if k:
            keys[k] = True
        out.append(i)
    return out


def probe(port: int, timeout: float = 3.0, opener=urllib.request.urlopen) -> dict:
    url = f"http://127.0.0.1:{port}/system_stats"
    try:
        with opener(url, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
        system = body.get("system", {}) if isinstance(body, dict) else {}
        devices = body.get("devices", []) if isinstance(body, dict) else []
        return {"port": port, "running": True, "url": f"http://127.0.0.1:{port}",
                "comfyui_version": system.get("comfyui_version"),
                "python_version": (system.get("python_version") or "").split(" ")[0] or None,
                "devices": [{"name": d.get("name"), "vram_total": d.get("vram_total"),
                             "vram_free": d.get("vram_free")} for d in devices if isinstance(d, dict)]}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"port": port, "running": False, "reason": type(e).__name__}


def collect(ports: list[int], search_roots: list[str] | None, deep: bool, opener=urllib.request.urlopen) -> dict:
    appdata = os.environ.get("APPDATA", "")
    installs = detect_comfy_desktop(appdata) + detect_legacy_desktop(appdata)
    if deep or search_roots:
        installs += scan_folders(search_roots or default_search_roots())
    ports = list(dict.fromkeys(ports + [i["last_known_port"] for i in installs if i.get("last_known_port")]))
    running = [probe(p, opener=opener) for p in ports]
    warnings = []
    for i in installs:
        lf = i.get("launch_flags") or {}
        if lf.get("listen") and lf["listen"] not in ("127.0.0.1", "localhost", "::1"):
            warnings.append(f"{i.get('name') or i['installPath']}: 起動引数に --listen {lf['listen']} があり、"
                            "PC外からComfyUIに接続できる設定の可能性があります（このSkillでは変更しません）。")
    installs = dedupe(installs)
    for i in installs:
        i["installPath_display"] = anonymize_path(i.get("installPath") or "")
        i["recorded_version"] = i.get("comfyVersionTag") or i.get("version")
        i["running_version"] = None
        port = i.get("last_known_port") or (i.get("launch_flags") or {}).get("port")
        for p in running:
            if p["running"] and (port is None or p["port"] == port) and i["kind"] != "comfy_cloud":
                i["running_version"] = p.get("comfyui_version")
                break
    return {"installations": installs, "probes": running,
            "running": [p for p in running if p["running"]], "warnings": warnings}


KIND_LABELS = {"comfy_desktop": "ComfyUI Desktop版", "legacy_desktop": "ComfyUI Desktop版（旧形式）",
               "portable": "ポータブル版", "folder": "フォルダ版"}


def human(r: dict, details: bool = False) -> list[str]:
    lines = ["=== ComfyUI（画像・動画を作るアプリ）の確認（読み取りのみ） ==="]
    local = [i for i in r["installations"] if i["kind"] != "comfy_cloud"]
    clouds = [i for i in r["installations"] if i["kind"] == "comfy_cloud"]
    for i in local:
        lines.append(f"- {KIND_LABELS.get(i['kind'], i['kind'])}  場所: {i['installPath_display']}")
        if i.get("running_version"):
            lines.append(f"    動いている版: {i['running_version']}")
        elif i.get("recorded_version"):
            lines.append(f"    記録上の版: {i['recorded_version']}（停止中のため、実際に動く版は起動後に正確に確認できます）")
        if details:
            if i.get("comfyui_dir"):
                lines.append(f"    本体フォルダ: {anonymize_path(i['comfyui_dir'])}")
            if i.get("last_known_port"):
                lines.append(f"    前回の待ち受けポート: {i['last_known_port']}")
            lf = i.get("launch_flags") or {}
            if lf.get("listen") or lf.get("port"):
                lines.append(f"    起動オプション: listen={lf.get('listen')} port={lf.get('port')}")
    if not local:
        lines.append("- このPCで動く ComfyUI は見つかりませんでした（--deep で追加検索できます）")
    if clouds and details:
        lines.append(f"- 対象外: {', '.join(c.get('name') or 'Comfy Cloud' for c in clouds)}（クラウド版。このPCでは動かない）")
    lines.append("")
    if r["running"]:
        for p in r["running"]:
            lines.append(f"状態: 起動中（{p['url']}、ComfyUI {p.get('comfyui_version')}）")
    else:
        tried = "・".join(str(p["port"]) for p in r["probes"])
        lines.append(f"状態: 停止中（このPCの {tried} 番の窓口に応答なし）。必要になったら起動を案内します。")
    for w in r["warnings"]:
        lines.append(f"[注意] {w}")
    if not details:
        lines.append("（詳しい情報は --details で表示できます）")
    return lines


def main(argv=None) -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--port", type=int, action="append", help="確認するポート（複数可）")
    ap.add_argument("--deep", action="store_true", help="よくある場所のフォルダも探す")
    ap.add_argument("--search-root", action="append", help="探すフォルダ（複数可）")
    ap.add_argument("--details", action="store_true", help="詳細を表示")
    ap.add_argument("--read-only", action="store_true", help="読み取り専用（このスクリプトは常に読み取り専用）")
    a = ap.parse_args(argv)
    r = collect(a.port or list(DEFAULT_PORTS), a.search_root, a.deep)
    emit(r, a.json, human(r, a.details))
    return 0


if __name__ == "__main__":
    sys.exit(main())
