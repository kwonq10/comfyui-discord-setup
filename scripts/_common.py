"""comfyui-discord-setup 共通ユーティリティ（標準ライブラリのみ）。

- 秘密情報のマスク（Discordトークン・Bearer・パスワード等）
- 接続先URLの検証（ループバック／LANのみ許可、インターネット上のホストは拒否）
- ユーザー名を含むパスの匿名化（%USERPROFILE% 置換）
- Windowsコンソールでも日本語が化けない出力
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
from urllib.parse import urlparse

# --- 出力 -------------------------------------------------------------------

def setup_stdout() -> None:
    """cp932 コンソールでも絵文字・日本語で落ちないようにする。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def emit(data: dict, as_json: bool, human_lines: list[str]) -> None:
    """結果を JSON か人間向けテキストで出力する。どちらも必ずマスク済み。"""
    if as_json:
        print(redact(json.dumps(data, ensure_ascii=False, indent=2)))
    else:
        for line in human_lines:
            print(redact(line))


# --- 秘密情報のマスク --------------------------------------------------------

# Discord Botトークン: base64(ユーザーID).タイムスタンプ.HMAC の3区切り
_DISCORD_TOKEN = re.compile(r"[A-Za-z0-9_\-]{23,28}\.[A-Za-z0-9_\-]{6,7}\.[A-Za-z0-9_\-]{27,}")
_MFA_TOKEN = re.compile(r"mfa\.[A-Za-z0-9_\-]{20,}")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-~+/=]{8,}")
_KEY_VALUE = re.compile(
    r"(?i)((?:discord_)?(?:token|api[_-]?key|password|passwd|secret|authorization|sessionkey)"
    r"[\"']?\s*[:=]\s*[\"']?)([^\s\"',}]{4,})"
)
_URL_CREDENTIALS = re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@")
_WEBHOOK = re.compile(r"(?i)(discord(?:app)?\.com/api/(?:v\d+/)?(?:webhooks|interactions)/\d+/)[A-Za-z0-9_\-]{20,}")

REDACTED = "<秘匿>"


def redact(text: str) -> str:
    """文字列から秘密情報らしき部分を <秘匿> に置き換える。"""
    if not text:
        return text
    text = _WEBHOOK.sub(lambda m: m.group(1) + REDACTED, text)
    text = _URL_CREDENTIALS.sub(lambda m: m.group(1) + REDACTED + "@", text)
    text = _DISCORD_TOKEN.sub(REDACTED, text)
    text = _MFA_TOKEN.sub(REDACTED, text)
    text = _BEARER.sub(lambda m: m.group(1) + REDACTED, text)
    text = _KEY_VALUE.sub(lambda m: m.group(1) + REDACTED, text)
    return text


def looks_secret(value: str) -> bool:
    """値そのものが秘密情報に見えるか（進捗ファイルへの保存拒否などに使う）。"""
    return redact(value) != value


# --- 新規導入の処理記録 -----------------------------------------------------

# setup_imagesmith.py install が導入先に作る処理記録（秘密情報なし）。
# この記録がある＝Skill が作ったフォルダ。status が complete でなければ導入は未完了。
INSTALL_STATE_FILE = ".comfy-discord-setup-install.json"
INSTALL_TOOL_ID = "comfyui-discord-setup"


# --- パスの匿名化 -----------------------------------------------------------

def anonymize_path(path: str) -> str:
    """ユーザー名を含むパスを %USERPROFILE% 形式にする（記録用）。"""
    if not path:
        return path
    home = os.path.expanduser("~")
    norm = os.path.normcase(os.path.normpath(path))
    home_norm = os.path.normcase(os.path.normpath(home))
    if norm == home_norm or norm.startswith(home_norm + os.sep):
        rest = os.path.normpath(path)[len(os.path.normpath(home)):]
        return "%USERPROFILE%" + rest
    return path


def expand_path(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


# --- 接続先URLの検証 --------------------------------------------------------

LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}


class UnsafeUrlError(ValueError):
    pass


def classify_host(host: str) -> str:
    """'loopback' / 'private'（LAN）/ 'public' / 'hostname' を返す。名前解決はしない。"""
    h = (host or "").strip("[]").lower()
    if h in LOCAL_HOSTNAMES:
        return "loopback"
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return "hostname"
    if ip.is_loopback:
        return "loopback"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_private or ip.is_link_local:
        return "private"
    return "public"


def validate_comfy_url(url: str, allow_lan: bool = False) -> str:
    """ComfyUI の接続先を検証し、正規化した URL を返す。

    既定ではこのPC内（127.0.0.1 / localhost）だけを許可する。
    allow_lan=True でも家庭内LAN（プライベートIP）まで。インターネット上の
    ホストや名前（DNS名）は ComfyUI を外部公開する構成につながるため拒否する。
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError("http:// または https:// で始まるURLを指定してください。")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("URLにユーザー名やパスワードを含めないでください。")
    if not parsed.hostname:
        raise UnsafeUrlError("URLにホスト名がありません。")
    kind = classify_host(parsed.hostname)
    if kind == "loopback":
        pass
    elif kind == "private" and allow_lan:
        pass
    elif kind == "private":
        raise UnsafeUrlError(
            "LAN上の別PCのアドレスです。このSkillは既定でこのPC内（127.0.0.1）のComfyUIのみ扱います。"
            "意図的な場合だけ --allow-lan を付けてください。"
        )
    elif kind == "unspecified":
        raise UnsafeUrlError("0.0.0.0 は接続先には使えません。127.0.0.1 を指定してください。")
    else:
        raise UnsafeUrlError(
            "インターネット上のホストまたはドメイン名です。ComfyUIを外部公開する構成はこのSkillでは扱いません。"
        )
    port = parsed.port
    if port is not None and not (1 <= port <= 65535):
        raise UnsafeUrlError("ポート番号が不正です。")
    netloc = parsed.hostname if ":" not in parsed.hostname else f"[{parsed.hostname}]"
    if port:
        netloc += f":{port}"
    return f"{parsed.scheme}://{netloc}"


def is_windows() -> bool:
    return os.name == "nt"
