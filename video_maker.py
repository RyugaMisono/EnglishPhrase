#!/usr/bin/env python3
"""
YouTube Shorts / TikTok 動画自動生成

使い方:
  python video_maker.py              # 次の未生成を1件生成
  python video_maker.py --no 5       # No.5 を生成
  python video_maker.py --preview    # 読み上げスクリプト確認のみ
  python video_maker.py --batch 10   # 最大10件まとめて生成
"""

import asyncio
import argparse
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

# ============================================================
# 定数
# ============================================================
VIDEO_W, VIDEO_H = 1080, 1920
CARD_PX  = 1080          # カード画像は正方形
HEADER_H = 150           # 上部ブランドヘッダー高さ
CARD_Y   = HEADER_H      # カード開始Y座標
SUB_Y    = CARD_Y + CARD_PX + 30   # 字幕開始Y座標

OUTPUT_DIR = Path("output")
VIDEOS_DIR = OUTPUT_DIR / "videos"
DATA_FILE  = OUTPUT_DIR / "generated.json"

# カラー（カードデザインに合わせる）
BG_COLOR  = (25, 55, 110)    # ネイビー
GOLD      = (210, 160, 0)    # ゴールド
WHITE     = (255, 255, 255)
BLACK     = (0, 0, 0)
CREAM     = (255, 252, 235)  # クリーム（カード背景と同系）

VOICE = "ja-JP-NanamiNeural"   # Microsoft Edge TTS 日本語女性


# ============================================================
# フォント検索
# ============================================================
def _find_font(bold: bool = False) -> str | None:
    candidates = []
    if bold:
        candidates += [
            "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJKjp-Bold.otf",
            "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf",
        ]
    else:
        candidates += [
            "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJKjp-Regular.otf",
            "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
        ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None


# ============================================================
# TTS（Edge TTS）
# ============================================================
async def _tts_async(text: str, out_path: str):
    import edge_tts
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(out_path)


def generate_tts(text: str, out_path: str):
    asyncio.run(_tts_async(text, out_path))


def _audio_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-i", path,
         "-show_entries", "format=duration",
         "-v", "quiet", "-of", "csv=p=0"],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


# ============================================================
# フレーム生成（PIL）
# ============================================================
def make_frame(card_path: str, subtitle: str) -> Image.Image:
    """1080×1920 の縦型フレームを生成"""
    font_bold   = _find_font(bold=True)
    font_normal = _find_font(bold=False)

    img  = Image.new("RGB", (VIDEO_W, VIDEO_H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # ── ヘッダー ────────────────────────────────────────
    draw.rectangle([(0, HEADER_H - 4), (VIDEO_W, HEADER_H)], fill=GOLD)

    fnt_h = ImageFont.truetype(font_bold, 44) if font_bold else ImageFont.load_default()
    title = "絶妙にテストに出ない英語"
    bb = draw.textbbox((0, 0), title, font=fnt_h)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    draw.text(((VIDEO_W - tw) // 2, (HEADER_H - th) // 2 - 4),
              title, font=fnt_h, fill=GOLD)

    # ── カード画像 ──────────────────────────────────────
    card = Image.open(card_path).convert("RGB").resize((CARD_PX, CARD_PX), Image.LANCZOS)
    img.paste(card, (0, CARD_Y))

    # ── 字幕 ────────────────────────────────────────────
    if subtitle:
        fnt_s = ImageFont.truetype(font_bold or font_normal, 50) if (font_bold or font_normal) \
                else ImageFont.load_default()
        lines = textwrap.wrap(subtitle, width=20)
        y = SUB_Y + 20
        for line in lines:
            bb = draw.textbbox((0, 0), line, font=fnt_s)
            tw = bb[2] - bb[0]
            draw.text(((VIDEO_W - tw) // 2, y), line, font=fnt_s,
                      fill=WHITE, stroke_width=3, stroke_fill=BLACK)
            y += (bb[3] - bb[1]) + 14

    return img


# ============================================================
# セグメント定義（カード×3）
# ============================================================
def build_segments(record: dict) -> list[tuple[str, str, str]]:
    """
    Returns [(card_path, tts_text, subtitle), ...]
    card_path  : 画像ファイルパス
    tts_text   : 読み上げテキスト
    subtitle   : 画面に表示する字幕テキスト
    """
    phrase   = record.get("phrase", "")
    meaning  = record.get("japanese_meaning", "")
    ex_en    = record.get("example_en", "")
    ex_ja    = record.get("example_ja", "")
    situation = record.get("situation", "")
    fun_fact = record.get("fun_fact", "")
    cards    = record.get("card_paths", [])

    if len(cards) < 3:
        raise ValueError(f"カード画像が3枚必要です（現在{len(cards)}枚）")

    def short(text: str, max_chars: int = 40) -> str:
        return text[:max_chars] + "…" if len(text) > max_chars else text

    return [
        (
            cards[0],
            f"今日のフレーズは「{phrase}」。日本語では「{meaning}」という意味です。",
            f"{phrase}\n{meaning}",
        ),
        (
            cards[1],
            f"使い方を見てみましょう。{ex_en}。{ex_ja}。{situation}",
            short(ex_ja),
        ),
        (
            cards[2],
            f"{fun_fact}。フォローして毎日新しいフレーズを学びましょう！",
            short(fun_fact),
        ),
    ]


# ============================================================
# 動画生成（ffmpeg）
# ============================================================
def _make_segment_video(card_path: str, tts_text: str, subtitle: str,
                        out_path: str, tmpdir: str):
    """1カード分の動画（静止画＋音声）を生成"""
    audio_path = os.path.join(tmpdir, f"audio_{Path(out_path).stem}.mp3")
    frame_path = os.path.join(tmpdir, f"frame_{Path(out_path).stem}.png")

    # TTS 音声
    generate_tts(tts_text, audio_path)
    duration = _audio_duration(audio_path) + 0.4  # 0.4s 余白

    # フレーム画像
    frame = make_frame(card_path, subtitle)
    frame.save(frame_path)

    # ffmpeg で静止画＋音声 → 動画
    subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", "30", "-i", frame_path,
        "-i", audio_path,
        "-c:v", "libx264", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "128k",
        "-t", str(duration),
        "-pix_fmt", "yuv420p",
        out_path,
    ], check=True, capture_output=True)


def make_video(record: dict, output_path: Path):
    """3カード分を結合した縦動画を生成"""
    segments = build_segments(record)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        seg_paths = []
        for i, (card_path, tts_text, subtitle) in enumerate(segments):
            seg_out = os.path.join(tmpdir, f"seg_{i}.mp4")
            print(f"  Card {i+1}/3 生成中...")
            _make_segment_video(card_path, tts_text, subtitle, seg_out, tmpdir)
            seg_paths.append(seg_out)

        # セグメントを結合
        list_file = os.path.join(tmpdir, "list.txt")
        with open(list_file, "w") as f:
            for p in seg_paths:
                f.write(f"file '{p}'\n")

        subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", list_file,
            "-c", "copy",
            str(output_path),
        ], check=True, capture_output=True)

    print(f"  完了: {output_path}")


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
    parser = argparse.ArgumentParser(description="Shorts/TikTok 動画生成")
    parser.add_argument("--no",      type=int, help="生成する投稿番号")
    parser.add_argument("--batch",   type=int, metavar="N", help="未生成をN件まとめて処理")
    parser.add_argument("--preview", action="store_true", help="読み上げスクリプト確認のみ")
    args = parser.parse_args()

    records = load_data()

    if args.preview:
        for r in records:
            if not r.get("video_path"):
                segs = build_segments(r)
                print(f"\nNo.{r['post_number']:03d}  {r['phrase']}")
                for i, (_, tts, sub) in enumerate(segs, 1):
                    print(f"  [{i}] TTS : {tts[:70]}...")
                    print(f"      字幕: {sub}")
                break
        return

    if args.no:
        targets = [r for r in records if r["post_number"] == args.no]
    elif args.batch:
        targets = [r for r in records if not r.get("video_path")][:args.batch]
    else:
        targets = [r for r in records if not r.get("video_path")][:1]

    if not targets:
        print("生成するコンテンツがありません（全件 video_path が存在）")
        return

    for record in targets:
        no   = record["post_number"]
        slug = record["phrase"].lower().replace(" ", "-")[:30]
        out  = VIDEOS_DIR / f"{no:03d}_{slug}.mp4"

        if out.exists():
            print(f"No.{no:03d} はスキップ（既存: {out}）")
            continue

        print(f"\nNo.{no:03d}  {record['phrase']}")
        try:
            make_video(record, out)
            for r in records:
                if r["post_number"] == no:
                    r["video_path"] = str(out)
                    break
            save_data(records)
        except Exception as e:
            print(f"  エラー: {e}")

    print(f"\n完了！ {VIDEOS_DIR} に保存されました")


if __name__ == "__main__":
    main()
