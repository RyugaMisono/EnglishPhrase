#!/usr/bin/env python3
"""
YouTube Shorts 自動アップロード

使い方:
  python youtube_uploader.py           # 次の未アップロードを1件
  python youtube_uploader.py --setup   # 接続確認
  python youtube_uploader.py --no 6    # No.6 を強制アップロード
  python youtube_uploader.py --dry-run # 確認のみ（アップロードしない）
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = Path("output")
VIDEOS_DIR = OUTPUT_DIR / "videos"
DATA_FILE  = OUTPUT_DIR / "generated.json"

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


# ============================================================
# YouTube 認証
# ============================================================
def _get_service():
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        print("pip3 install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
        sys.exit(1)

    refresh_token   = os.getenv("YOUTUBE_REFRESH_TOKEN")
    client_id       = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret   = os.getenv("YOUTUBE_CLIENT_SECRET")

    if not all([refresh_token, client_id, client_secret]):
        print("エラー: .env に YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN を設定してください")
        print("       まず python youtube_auth.py を実行してください")
        sys.exit(1)

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


# ============================================================
# アップロード
# ============================================================
def build_metadata(record: dict) -> tuple[str, str, list[str]]:
    """タイトル・説明文・タグを生成"""
    phrase  = record.get("phrase", "")
    meaning = record.get("japanese_meaning", "")
    caption = record.get("instagram_caption", "")
    tags    = record.get("hashtags", [])

    title = f'"{phrase}" ← テストに出ない英語 #Shorts'[:100]

    description = f"""{caption}

📚 絶妙にテストに出ない英単語帳
ネイティブが使うのに試験には出てこない英語フレーズを毎日紹介

#英語学習 #英語フレーズ #Shorts #英語"""

    yt_tags = list(dict.fromkeys(
        ["英語学習", "英語フレーズ", "ネイティブ英語", "Shorts", "英語", "テストに出ない英語"]
        + [t for t in tags if t]
    ))[:15]

    return title, description, yt_tags


def upload_shorts(record: dict, video_path: Path) -> str:
    """YouTube Shorts にアップロードしてビデオIDを返す"""
    from googleapiclient.http import MediaFileUpload

    youtube = _get_service()
    title, description, tags = build_metadata(record)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "27",   # Education
            "defaultLanguage": "ja",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    print(f"  アップロード中: {video_path.name}")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            print(f"  ... {pct}%", end="\r")

    video_id = response["id"]
    print(f"  完了: https://youtube.com/shorts/{video_id}")
    return video_id


# ============================================================
# データ管理
# ============================================================
def load_data() -> list[dict]:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def save_data(records: list[dict]):
    DATA_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
# メイン
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup",   action="store_true", help="接続確認")
    parser.add_argument("--no",      type=int,            help="投稿番号を指定")
    parser.add_argument("--dry-run", action="store_true", help="確認のみ")
    args = parser.parse_args()

    if args.setup:
        print("YouTube 接続確認中...")
        svc = _get_service()
        ch = svc.channels().list(part="snippet", mine=True).execute()
        items = ch.get("items", [])
        if items:
            name = items[0]["snippet"]["title"]
            cid  = items[0]["id"]
            print(f"  チャンネル: {name} ({cid})")
        else:
            print("  チャンネルが見つかりません")
        return

    records = load_data()

    if args.no:
        targets = [r for r in records if r["post_number"] == args.no]
    else:
        # 動画あり・YouTube未アップロードの次の1件
        targets = [
            r for r in records
            if r.get("video_path")
            and Path(r["video_path"]).exists()
            and not r.get("youtube_id")
        ][:1]

    if not targets:
        print("アップロード対象がありません（video_maker.py を先に実行してください）")
        return

    for record in targets:
        no         = record["post_number"]
        video_path = Path(record.get("video_path", ""))

        if not video_path.exists():
            print(f"No.{no:03d}: 動画ファイルが見つかりません: {video_path}")
            print("  python video_maker.py を先に実行してください")
            continue

        title, _, _ = build_metadata(record)
        print(f"\nNo.{no:03d}  {record['phrase']}")
        print(f"  タイトル: {title}")

        if args.dry_run:
            print("  [dry-run] アップロードをスキップ")
            continue

        try:
            video_id = upload_shorts(record, video_path)
            for r in records:
                if r["post_number"] == no:
                    r["youtube_id"]       = video_id
                    r["youtube_url"]      = f"https://youtube.com/shorts/{video_id}"
                    r["youtube_uploaded"] = datetime.now().isoformat()
                    break
            save_data(records)
        except Exception as e:
            print(f"  エラー: {e}")


if __name__ == "__main__":
    main()
