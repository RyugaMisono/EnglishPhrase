#!/usr/bin/env python3
"""
YouTube OAuth 認証（初回のみ実行）

使い方:
  1. client_secrets.json をこのフォルダに置く
  2. python youtube_auth.py
  3. ブラウザで認証 → 表示された値を .env と GitHub Secrets に追加
"""

import json
from pathlib import Path

CLIENT_SECRETS = Path("client_secrets.json")
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def main():
    if not CLIENT_SECRETS.exists():
        print("""
client_secrets.json が見つかりません。

【手順】
1. https://console.cloud.google.com/ を開く
2. プロジェクト作成（または選択）
3. APIとサービス → ライブラリ → "YouTube Data API v3" を有効化
4. APIとサービス → 認証情報 → 認証情報を作成 → OAuthクライアントID
5. アプリケーションの種類: デスクトップアプリ
6. 作成後「JSONをダウンロード」→ このフォルダに client_secrets.json として保存
""")
        return

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("pip3 install google-auth-oauthlib を実行してください")
        return

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS), SCOPES)
    creds = flow.run_local_server(port=0)

    data = json.loads(CLIENT_SECRETS.read_text())
    client = data.get("installed") or data.get("web", {})

    print("\n=== 認証成功！以下を .env と GitHub Secrets に追加 ===\n")
    print(f"YOUTUBE_CLIENT_ID={client.get('client_id', creds.client_id)}")
    print(f"YOUTUBE_CLIENT_SECRET={client.get('client_secret', creds.client_secret)}")
    print(f"YOUTUBE_REFRESH_TOKEN={creds.refresh_token}")
    print("\n次のコマンドで接続確認:")
    print("  python youtube_uploader.py --setup")


if __name__ == "__main__":
    main()
