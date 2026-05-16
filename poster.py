#!/usr/bin/env python3
"""
自動投稿スクリプト

使い方:
  python poster.py              # 次の未投稿を1件投稿（Instagram + X）
  python poster.py --instagram  # Instagramのみ
  python poster.py --x          # X(Twitter)のみ
  python poster.py --dry-run    # 投稿せず内容だけ確認
  python poster.py --status     # 投稿状況を表示

事前準備:
  .env に以下を設定:
    INSTAGRAM_USER_ID=...
    INSTAGRAM_ACCESS_TOKEN=...
    IMGBB_API_KEY=...         # 画像ホスティング（無料）
    X_API_KEY=...
    X_API_SECRET=...
    X_ACCESS_TOKEN=...
    X_ACCESS_SECRET=...
"""

import os
import sys
import json
import time
import base64
import argparse
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = Path("output")
DATA_FILE  = OUTPUT_DIR / "generated.json"


# ============================================================
# データ管理
# ============================================================
def load_data() -> list[dict]:
    if not DATA_FILE.exists():
        print("エラー: output/generated.json がありません。先に main.py を実行してください。")
        sys.exit(1)
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def save_data(records: list[dict]):
    DATA_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def next_unposted(records: list[dict]) -> tuple[int, dict] | None:
    for i, r in enumerate(records):
        if not r.get("posted_at"):
            return i, r
    return None


# ============================================================
# 画像ホスティング（imgbb - 無料API）
# ============================================================
def upload_to_imgbb(image_path: str) -> str:
    """
    imgbbに画像をアップロードして公開URLを返す。
    APIキー取得: https://api.imgbb.com/ （無料・クレカ不要）
    """
    api_key = os.getenv("IMGBB_API_KEY")
    if not api_key:
        raise ValueError("IMGBB_API_KEY が .env に設定されていません")

    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    data = urllib.parse.urlencode({
        "key":   api_key,
        "image": image_b64,
    }).encode("utf-8")

    req  = urllib.request.Request("https://api.imgbb.com/1/upload", data=data)
    resp = urllib.request.urlopen(req, timeout=30)
    result = json.loads(resp.read().decode("utf-8"))

    if not result.get("success"):
        raise RuntimeError(f"imgbbアップロード失敗: {result}")

    return result["data"]["url"]


# ============================================================
# Instagram Graph API
# ============================================================
def _ig_request(method: str, endpoint: str, params: dict = None) -> dict:
    base = "https://graph.instagram.com/v21.0"
    params = params or {}
    params["access_token"] = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")

    if method == "GET":
        qs  = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{base}{endpoint}?{qs}")
    else:
        data = urllib.parse.urlencode(params).encode("utf-8")
        req  = urllib.request.Request(f"{base}{endpoint}", data=data)

    try:
        resp   = urllib.request.urlopen(req, timeout=30)
        return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise RuntimeError(f"Instagram APIエラー {e.code}: {body}")


def post_instagram_carousel(record: dict) -> str:
    """3枚カルーセルをInstagramに投稿してメディアIDを返す"""
    user_id = os.getenv("INSTAGRAM_USER_ID")
    if not user_id:
        raise ValueError("INSTAGRAM_USER_ID が .env に設定されていません")

    card_paths = record.get("card_paths", [])
    if len(card_paths) < 3:
        raise ValueError("カード画像が3枚ありません。先に main.py を実行してください。")

    # Step 1: 各画像をimgbbにアップロードして公開URLを取得
    print("  画像をアップロード中...")
    media_ids = []
    for i, path in enumerate(card_paths, 1):
        print(f"    Card {i}: {Path(path).name} → imgbb...")
        url = upload_to_imgbb(path)
        # カルーセルアイテムとして登録
        result = _ig_request("POST", f"/{user_id}/media", {
            "image_url":        url,
            "is_carousel_item": "true",
        })
        media_ids.append(result["id"])
        time.sleep(1)

    # Step 2: カルーセルコンテナ作成
    caption = build_instagram_caption(record)
    result  = _ig_request("POST", f"/{user_id}/media", {
        "media_type": "CAROUSEL",
        "children":   ",".join(media_ids),
        "caption":    caption,
    })
    carousel_id = result["id"]

    # Step 3: 公開
    time.sleep(2)
    result = _ig_request("POST", f"/{user_id}/media_publish", {
        "creation_id": carousel_id,
    })
    return result["id"]


def build_instagram_caption(record: dict) -> str:
    caption = record.get("instagram_caption", "")
    tags    = " ".join(f"#{t}" for t in record.get("hashtags", []))
    return f"{caption}\n\n{tags}"


# ============================================================
# Buffer API
# ============================================================
BUFFER_GQL = "https://api.buffer.com"


def _gql(query: str, variables: dict = None) -> dict:
    """Buffer GraphQL APIを呼ぶ"""
    token = os.getenv("BUFFER_ACCESS_TOKEN", "")
    if not token:
        raise ValueError("BUFFER_ACCESS_TOKEN が .env に設定されていません")
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req  = urllib.request.Request(
        BUFFER_GQL,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        },
    )
    try:
        resp   = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read().decode("utf-8"))
        if "errors" in result:
            raise RuntimeError(f"GraphQLエラー: {result['errors']}")
        return result.get("data", {})
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Buffer APIエラー {e.code}: {e.read().decode()}")


def get_buffer_profiles() -> dict[str, str]:
    """接続済みチャンネル一覧を返す {service: channel_id}"""
    data = _gql("query { account { channels { id name service } } }")
    channels = data.get("account", {}).get("channels", [])
    return {ch["service"]: ch["id"] for ch in channels}


def _next_post_time(offset_days: int = 1) -> str:
    """毎朝8:30 JST の投稿時刻をUTC文字列で返す"""
    from datetime import timezone, timedelta
    jst      = timezone(timedelta(hours=9))
    base     = datetime.now(jst).replace(hour=8, minute=30, second=0, microsecond=0)
    post_jst = base + timedelta(days=offset_days)
    return post_jst.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _buffer_create_post(channel_id: str, text: str,
                        image_urls: list[str] = None,
                        due_at: str = None,
                        is_instagram: bool = False) -> dict:
    """Buffer に1件投稿をスケジュール追加"""
    inp: dict = {
        "channelId":     channel_id,
        "text":          text,
        "schedulingType": "automatic",
        "mode":          "customScheduled",
        "dueAt":         due_at or _next_post_time(1),
    }
    if image_urls:
        inp["assets"] = [{"image": {"url": u}} for u in image_urls]
    if is_instagram:
        inp["metadata"] = {"instagram": {"type": "post", "shouldShareToFeed": True}}

    mutation = """
    mutation CreatePost($input: CreatePostInput!) {
      createPost(input: $input) {
        ... on PostActionSuccess { post { id } }
        ... on NotFoundError     { message }
        ... on UnauthorizedError { message }
        ... on UnexpectedError   { message }
        ... on RestProxyError    { message code }
        ... on LimitReachedError { message }
        ... on InvalidInputError { message }
      }
    }
    """
    result = _gql(mutation, {"input": inp})
    payload = result.get("createPost", {})
    if "message" in payload:
        raise RuntimeError(f"Buffer投稿エラー: {payload['message']}")
    return payload


def add_to_buffer_queue(record: dict, offset_days: int = 1) -> dict:
    """Instagram（カルーセル）と X をまとめて Buffer キューに追加"""
    profiles   = get_buffer_profiles()
    card_paths = record.get("card_paths", [])
    results    = {}

    # ── 画像を imgbb にアップロード ─────────────────────────
    image_urls = []
    if card_paths:
        print("  画像をimgbbにアップロード中...")
        for i, path in enumerate(card_paths, 1):
            print(f"    Card {i} → imgbb...")
            image_urls.append(upload_to_imgbb(path))
            time.sleep(0.8)

    due_at = _next_post_time(offset_days)

    # ── Instagram ──────────────────────────────────────────
    ig_id = profiles.get("instagram")
    if ig_id and image_urls:
        results["instagram"] = _buffer_create_post(
            ig_id,
            build_instagram_caption(record),
            image_urls=image_urls,
            due_at=due_at,
            is_instagram=True,
        )
        print("  ✓ Instagram → Buffer キューに追加")

    # ── X (Twitter) ────────────────────────────────────────
    x_id = profiles.get("twitter") or profiles.get("x")
    if x_id:
        results["x"] = _buffer_create_post(
            x_id,
            record.get("x_post", ""),
            image_urls=image_urls[:1] if image_urls else None,
            due_at=due_at,
        )
        print("  ✓ X → Buffer キューに追加")

    if not ig_id and not x_id:
        raise RuntimeError("Instagram / X のどちらも Buffer に接続されていません。\nbuffer.com でアカウントを接続してください。")

    return results


# ============================================================
# X (Twitter) API v2
# ============================================================
def post_x(record: dict) -> str:
    """X(Twitter)にテキスト投稿してツイートIDを返す"""
    import hmac
    import hashlib
    import secrets

    api_key        = os.getenv("X_API_KEY", "")
    api_secret     = os.getenv("X_API_SECRET", "")
    access_token   = os.getenv("X_ACCESS_TOKEN", "")
    access_secret  = os.getenv("X_ACCESS_SECRET", "")

    if not all([api_key, api_secret, access_token, access_secret]):
        raise ValueError("X APIキーが .env に設定されていません")

    text = record.get("x_post", "")
    url  = "https://api.twitter.com/2/tweets"

    # OAuth 1.0a 署名
    ts    = str(int(time.time()))
    nonce = secrets.token_hex(16)
    oauth_params = {
        "oauth_consumer_key":     api_key,
        "oauth_nonce":            nonce,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp":        ts,
        "oauth_token":            access_token,
        "oauth_version":          "1.0",
    }

    # 署名ベース文字列
    all_params  = {**oauth_params}
    sorted_params = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
        for k, v in sorted(all_params.items())
    )
    base_string = "&".join([
        "POST",
        urllib.parse.quote(url, safe=""),
        urllib.parse.quote(sorted_params, safe=""),
    ])
    signing_key = f"{urllib.parse.quote(api_secret, safe='')}&{urllib.parse.quote(access_secret, safe='')}"
    signature   = base64.b64encode(
        hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    ).decode()
    oauth_params["oauth_signature"] = signature

    auth_header = "OAuth " + ", ".join(
        f'{urllib.parse.quote(k, safe="")}="{urllib.parse.quote(v, safe="")}"'
        for k, v in sorted(oauth_params.items())
    )

    body = json.dumps({"text": text}).encode("utf-8")
    req  = urllib.request.Request(url, data=body, headers={
        "Authorization": auth_header,
        "Content-Type":  "application/json",
    })

    try:
        resp   = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read().decode("utf-8"))
        return result["data"]["id"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise RuntimeError(f"X APIエラー {e.code}: {body}")


# ============================================================
# ストック残量チェック → 自動補充
# ============================================================
def check_and_refill(records: list[dict], threshold: int = 30):
    """
    未投稿 & 未生成のストックが threshold 件を下回ったら
    Claudeに新しいフレーズを生成させて phrase.txt に追記する
    """
    from main import parse_phrases, generate, load_data as load_gen_data, save_data as save_gen_data
    import anthropic

    unposted = [r for r in records if not r.get("posted_at")]
    if len(unposted) > threshold:
        return

    print(f"\n[補充] 未投稿ストックが{len(unposted)}件です。新しいフレーズを生成します...")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("  警告: ANTHROPIC_API_KEY がないため補充をスキップします")
        return

    # 既存フレーズ一覧（重複回避）
    phrase_file = Path("phrase.txt")
    existing    = [r["phrase"] for r in records]

    client = anthropic.Anthropic(api_key=api_key)
    prompt = f"""「絶妙にテストに出ない英単語帳」シリーズ用の新しい英語フレーズを20個生成してください。

条件:
- ネイティブが日常会話で自然に使う
- TOEIC・センター試験・英検には出ない
- 日本人があまり知らない表現
- スラング・慣用句・口語表現を中心に

以下は既存リストなので重複させないでください:
{", ".join(existing[:50])}

JSON配列のみ出力（他の文章不要）:
[
  {{"phrase": "...", "ja_hint": "日本語の意味"}},
  ...
]"""

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    import re
    text  = resp.content[0].text
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        print("  警告: フレーズ生成のJSON解析に失敗しました")
        return

    new_phrases = json.loads(match.group())
    existing_lower = {p.lower() for p in existing}

    added = 0
    with open(phrase_file, "a", encoding="utf-8") as f:
        f.write("\n# 自動補充\n")
        for p in new_phrases:
            phrase = p.get("phrase", "").strip()
            ja     = p.get("ja_hint", "")
            if not phrase or phrase.lower() in existing_lower:
                continue
            line = f"{phrase}  {ja}" if ja else phrase
            f.write(f"{line}\n")
            existing_lower.add(phrase.lower())
            added += 1

    print(f"  ✓ {added}件を phrase.txt に追記しました")

    # 追記分もすぐ生成する（5件だけ先行して処理）
    all_phrases  = parse_phrases(phrase_file, Path("phrase_unusable.txt"))
    generated    = load_gen_data()
    done_set     = {r["phrase"].lower() for r in generated}
    new_targets  = [p for p in all_phrases if p["phrase"].lower() not in done_set][:5]

    for p in new_targets:
        no = len(generated) + 1
        print(f"  生成中: {p['phrase']}...")
        try:
            from main import save_cards
            data            = generate(p, no, client)
            data["card_paths"] = save_cards(data, no)
            generated.append(data)
        except Exception as e:
            print(f"  エラー: {e}")
    save_gen_data(generated)


# ============================================================
# メイン
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Instagram + X 自動投稿")
    parser.add_argument("--instagram",     action="store_true", help="Instagramのみ直接投稿")
    parser.add_argument("--x",             action="store_true", help="X(Twitter)のみ直接投稿")
    parser.add_argument("--buffer",        action="store_true", help="Buffer経由で投稿（推奨）")
    parser.add_argument("--buffer-batch",  type=int, metavar="N", help="N件まとめてBufferキューに追加")
    parser.add_argument("--setup-buffer",  action="store_true", help="Buffer接続プロフィールを確認")
    parser.add_argument("--dry-run",       action="store_true", help="投稿せず内容だけ確認")
    parser.add_argument("--status",        action="store_true", help="投稿状況を表示")
    args = parser.parse_args()

    do_instagram = not args.x
    do_x         = not args.instagram

    records = load_data()

    # --setup-buffer
    if args.setup_buffer:
        print("\nBuffer 接続プロフィール確認中...")
        try:
            profiles = get_buffer_profiles()
            if not profiles:
                print("接続されているプロフィールがありません。\nbuffer.com でアカウントを接続してください。")
            for service, pid in profiles.items():
                print(f"  {service:12s} : {pid}")
        except Exception as e:
            print(f"エラー: {e}")

        for enum_name in ("InstagramPostMetadataInput", "AssetInput", "ImageAssetInput", "SchedulingType", "ShareMode"):
            print(f"\n{enum_name} 確認中...")
            try:
                intro = _gql(f"""
                {{
                  __type(name: "{enum_name}") {{
                    kind
                    enumValues {{ name }}
                    inputFields {{
                      name
                      type {{ name kind ofType {{ name }} }}
                    }}
                  }}
                }}
                """)
                t = intro.get("__type") or {}
                if t.get("enumValues"):
                    print("  " + ", ".join(v["name"] for v in t["enumValues"]))
                elif t.get("inputFields"):
                    for f in t["inputFields"]:
                        typ = f["type"]
                        print(f"  {f['name']}: {typ.get('name') or (typ.get('ofType') or {}).get('name')}")
                else:
                    print(f"  kind={t.get('kind')}  (no fields)")
            except Exception as e:
                print(f"  エラー: {e}")
        return

    # --status
    if args.status:
        posted   = [r for r in records if r.get("posted_at")]
        unposted = [r for r in records if not r.get("posted_at")]
        print(f"\n投稿済み : {len(posted)}件")
        print(f"未投稿   : {len(unposted)}件")
        if unposted:
            print(f"\n次回投稿予定: No.{unposted[0]['post_number']:03d}  {unposted[0]['phrase']}")
        return

    # 次の未投稿を取得
    result = next_unposted(records)
    if result is None:
        print("全コンテンツ投稿済みです！")
        check_and_refill(records)
        return

    idx, record = result
    no   = record["post_number"]
    kind = "[NG] " if record.get("is_ng") else ""
    print(f"\nNo.{no:03d}  {kind}{record['phrase']}")
    print(f"  意味  : {record['japanese_meaning']}")
    print(f"  X投稿 : {record['x_post'][:60]}...")

    if args.dry_run:
        print("\n[dry-run] 投稿はスキップしました")
        return

    # --buffer-batch: N件まとめてキューに追加
    if args.buffer_batch:
        targets = [r for r in records if not r.get("posted_at")][:args.buffer_batch]
        if not targets:
            print("未投稿コンテンツがありません。")
            return
        print(f"\n{len(targets)}件をBufferキューに追加します...\n")
        for i, rec in enumerate(targets, 1):
            idx = records.index(rec)
            print(f"[{i}/{len(targets)}] No.{rec['post_number']:03d}  {rec['phrase']}")
            try:
                add_to_buffer_queue(rec, offset_days=i)
                records[idx]["posted_at"]   = datetime.now().isoformat()
                records[idx]["posted_info"] = {"buffer": "queued"}
                save_data(records)
                time.sleep(1)
            except Exception as e:
                print(f"  ✗ エラー: {e}")
        print(f"\n完了！ buffer.com でスケジュールを確認してください。")
        check_and_refill(records)
        return

    # --buffer: 1件をBufferキューに追加
    if args.buffer:
        print(f"\n  Bufferキューに追加中...")
        try:
            add_to_buffer_queue(record)
            records[idx]["posted_at"]   = datetime.now().isoformat()
            records[idx]["posted_info"] = {"buffer": "queued"}
            save_data(records)
            print(f"  完了！ buffer.com でスケジュールを確認してください。")
        except Exception as e:
            print(f"  ✗ エラー: {e}")
        check_and_refill(records)
        return

    posted_info = {}

    # Instagram投稿
    if do_instagram:
        try:
            print("\n  Instagram に投稿中...")
            media_id = post_instagram_carousel(record)
            posted_info["instagram_media_id"] = media_id
            print(f"  ✓ Instagram 投稿完了 (id: {media_id})")
        except Exception as e:
            print(f"  ✗ Instagram エラー: {e}")

    # X投稿
    if do_x:
        try:
            print("  X に投稿中...")
            tweet_id = post_x(record)
            posted_info["x_tweet_id"] = tweet_id
            print(f"  ✓ X 投稿完了 (id: {tweet_id})")
        except Exception as e:
            print(f"  ✗ X エラー: {e}")

    # 投稿済みマーク
    if posted_info:
        records[idx]["posted_at"]   = datetime.now().isoformat()
        records[idx]["posted_info"] = posted_info
        save_data(records)

    # ストック残量チェック
    check_and_refill(records)


if __name__ == "__main__":
    main()
