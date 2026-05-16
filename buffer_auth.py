#!/usr/bin/env python3
"""
Buffer アクセストークン取得ツール

使い方:
  python3 buffer_auth.py

事前準備:
  buffer.com/developers → アプリ → Callback URLs に以下を追加:
    https://localhost/callback
"""

import urllib.request
import urllib.parse
import json
import webbrowser
from pathlib import Path

TOKEN_URL     = "https://api.bufferapp.com/1/oauth2/token.json"
AUTH_BASE_URL = "https://bufferapp.com/oauth2/authorize"
REDIRECT_URI  = "https://localhost/callback"


def main():
    print("\n=== Buffer アクセストークン取得 ===\n")
    print("buffer.com/developers → アプリ → App Info を開いてください\n")

    client_id     = input("Client ID     : ").strip()
    client_secret = input("Client Secret : ").strip()

    if not client_id or not client_secret:
        print("Client ID と Client Secret を入力してください。")
        return

    # Step 1: 認証URL を開く
    auth_url = AUTH_BASE_URL + "?" + urllib.parse.urlencode({
        "client_id":     client_id,
        "redirect_uri":  REDIRECT_URI,
        "response_type": "code",
    })

    print("\nブラウザでBufferの認証ページを開きます...")
    webbrowser.open(auth_url)

    print("""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【手順】
1. ブラウザで「Allow Access」をクリック
2. エラーページ（接続できません）が出るのは正常です
3. ブラウザのアドレスバーのURLをコピーしてください

例: https://localhost/callback?code=xxxxxxxxxxxxxxxx
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")

    redirected_url = input("アドレスバーのURLを貼り付け: ").strip()

    # URLからcodeを抽出
    try:
        parsed    = urllib.parse.urlparse(redirected_url)
        params    = urllib.parse.parse_qs(parsed.query)
        auth_code = params.get("code", [None])[0]
    except Exception:
        auth_code = None

    if not auth_code:
        print("\nURLからコードを取得できませんでした。")
        print("アドレスバーのURL全体をコピーしてください。")
        return

    print(f"\nコード取得成功: {auth_code[:10]}...")

    # Step 2: アクセストークンを取得
    data = urllib.parse.urlencode({
        "client_id":     client_id,
        "client_secret": client_secret,
        "redirect_uri":  REDIRECT_URI,
        "code":          auth_code,
        "grant_type":    "authorization_code",
    }).encode("utf-8")

    try:
        req    = urllib.request.Request(TOKEN_URL, data=data)
        resp   = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"\nエラー: {e.code} {e.read().decode()}")
        return

    access_token = result.get("access_token")
    if not access_token:
        print(f"\nトークン取得失敗: {result}")
        return

    # Step 3: .env に書き込む
    env_path = Path(".env")
    lines    = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []

    # 既存のBUFFER_ACCESS_TOKENを上書き or 追記
    updated = False
    for i, line in enumerate(lines):
        if line.startswith("BUFFER_ACCESS_TOKEN="):
            lines[i] = f"BUFFER_ACCESS_TOKEN={access_token}"
            updated  = True
            break
    if not updated:
        lines.append(f"BUFFER_ACCESS_TOKEN={access_token}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n✓ .env に保存しました！")
    print(f"\n次のコマンドで接続確認:")
    print(f"  python3 poster.py --setup-buffer")


if __name__ == "__main__":
    main()
