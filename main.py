#!/usr/bin/env python3
"""
絶妙にテストに出ない英単語帳 - コンテンツ自動生成スクリプト

使い方:
  python main.py              # 次の未処理フレーズを1件処理
  python main.py --count 5    # 5件まとめて処理
  python main.py --all        # 全件処理
  python main.py --list       # フレーズ一覧表示（処理状況付き）
  python main.py --preview 3  # 画像を生成せず内容だけ確認
  python main.py --ng         # NGシリーズのみ処理

NGシリーズ: phrase_unusable.txt に記載 または phrase.txt で [NG] プレフィックス
費用目安: claude-haiku使用で1件あたり約0.2円
"""

import os
import json
import re
import sys
import argparse
from pathlib import Path
from datetime import datetime

import anthropic
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# 設定
# ============================================================
PHRASE_FILE    = Path("phrase.txt")
PHRASE_NG_FILE = Path("phrase_unusable.txt")
OUTPUT_DIR     = Path("output")
CARDS_DIR      = OUTPUT_DIR / "cards"
DATA_FILE      = OUTPUT_DIR / "generated.json"
CARD_W, CARD_H = 1080, 1080

# カラーパレット
C = {
    "bg":       (255, 252, 235),
    "header":   (25,  55, 110),
    "red":      (190, 30,  30),
    "amber":    (100, 68,  12),
    "amber_lt": (245, 232, 195),
    "ng_red":   (160, 20,  20),
    "text":     (25,  25,  25),
    "muted":    (120, 110, 90),
    "gold":     (210, 160, 0),
    "border":   (200, 188, 155),
    "white":    (255, 255, 255),
    "dot":      (215, 208, 192),
}

# ============================================================
# フォント
# ============================================================
_FONT_PATHS = {
    "ja": [
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴ ProN W3.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ],
    "ja_bold": [
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴ ProN W6.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    ],
    "en": [
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/System/Library/Fonts/Times.ttc",
    ],
    "en_bold": [
        "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
    ],
}

_font_cache: dict = {}

def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    key = (kind, size)
    if key in _font_cache:
        return _font_cache[key]
    for path in _FONT_PATHS.get(kind, []):
        if Path(path).exists():
            try:
                f = ImageFont.truetype(path, size)
                _font_cache[key] = f
                return f
            except Exception:
                continue
    f = ImageFont.load_default()
    _font_cache[key] = f
    return f


# ============================================================
# ビジュアルユーティリティ
# ============================================================
def draw_dot_bg(draw: ImageDraw.ImageDraw, spacing: int = 42, radius: float = 1.8):
    """微細なドットグリッド背景"""
    for x in range(0, CARD_W + spacing, spacing):
        for y in range(0, CARD_H + spacing, spacing):
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=C["dot"])


def draw_corners(draw: ImageDraw.ImageDraw, margin: int = 28, size: int = 16, color: tuple = None):
    """4隅にダイアモンド装飾"""
    color = color or C["border"]
    for cx, cy in [(margin, margin), (CARD_W - margin, margin),
                   (margin, CARD_H - margin), (CARD_W - margin, CARD_H - margin)]:
        draw.polygon([
            (cx,             cy - size // 2),
            (cx + size // 2, cy),
            (cx,             cy + size // 2),
            (cx - size // 2, cy),
        ], fill=color)


def draw_ng_badge(img: Image.Image) -> Image.Image:
    """右上に斜め「x 使えない！」スタンプ"""
    bw, bh = 300, 58
    badge = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    bd = ImageDraw.Draw(badge)
    bd.rectangle([0, 0, bw - 1, bh - 1], fill=(*C["ng_red"], 220))
    bd.text((bw // 2, bh // 2), "x  日常で使えない！",
            font=font("ja_bold", 26), fill=(255, 255, 255, 255), anchor="mm")
    rotated = badge.rotate(35, expand=True, resample=Image.BICUBIC)
    base = img.convert("RGBA")
    base.paste(rotated, (CARD_W - rotated.width + 10, 55), rotated)
    return base.convert("RGB")


def ornament_line(draw: ImageDraw.ImageDraw, y: int, color: tuple = None, pad: int = 120):
    """中央に◆を置いた装飾区切り線"""
    color = color or C["border"]
    cx, size = CARD_W // 2, 7
    draw.line([pad, y, cx - 28, y], fill=color, width=1)
    draw.line([cx + 28, y, CARD_W - pad, y], fill=color, width=1)
    draw.polygon([
        (cx, y - size), (cx + size, y), (cx, y + size), (cx - size, y)
    ], fill=color)


def wrap(text: str, fnt, max_w: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """英語・日本語両対応テキスト折り返し"""
    has_ja = bool(re.search(r"[぀-ヿ一-鿿]", text))
    lines, current = [], ""
    if has_ja:
        for ch in text:
            test = current + ch
            if draw.textbbox((0, 0), test, font=fnt)[2] > max_w and current:
                lines.append(current)
                current = ch
            else:
                current = test
    else:
        for word in text.split():
            test = f"{current} {word}".strip()
            if draw.textbbox((0, 0), test, font=fnt)[2] > max_w and current:
                lines.append(current)
                current = word
            else:
                current = test
    if current:
        lines.append(current)
    return lines


def text_center(draw, y, text, fnt, color):
    draw.text((CARD_W // 2, y), text, font=fnt, fill=color, anchor="mm")


def text_block(draw, y, lines, fnt, color, lh):
    for line in lines:
        draw.text((CARD_W // 2, y), line, font=fnt, fill=color, anchor="mm")
        y += lh
    return y


def hline(draw, y, color=None, pad=80, width=2):
    draw.line([pad, y, CARD_W - pad, y], fill=(color or C["border"]), width=width)


# ============================================================
# カード生成
# ============================================================
def card1(data: dict, no: int) -> Image.Image:
    """カード1: フレーズ + 評価"""
    is_ng = data.get("is_ng", False)
    img = Image.new("RGB", (CARD_W, CARD_H), C["bg"])
    d   = ImageDraw.Draw(img)
    draw_dot_bg(d)

    # ヘッダー
    d.rectangle([0, 0, CARD_W, 105], fill=C["header"])
    d.text((CARD_W // 2, 52), "絶妙にテストに出ない英単語帳", font=font("ja", 30), fill=C["white"], anchor="mm")
    d.text((CARD_W - 30, 85), f"No. {no:03d}", font=font("en", 22), fill=C["gold"], anchor="rs")

    # メインフレーズ
    phrase = data["phrase"]
    fsize  = 68 if len(phrase) <= 14 else 50 if len(phrase) <= 22 else 38
    f_ph   = font("en_bold", fsize)
    text_center(d, 370, phrase, f_ph, C["text"])

    # 二重アンダーライン
    bb = d.textbbox((CARD_W // 2, 370), phrase, font=f_ph, anchor="mm")
    d.line([bb[0] - 8, bb[3] + 10, bb[2] + 8, bb[3] + 10], fill=C["red"], width=4)
    d.line([bb[0] - 8, bb[3] + 17, bb[2] + 8, bb[3] + 17], fill=C["red"], width=1)

    hline(d, 485, pad=60)

    # 評価（NGシリーズは「ドラマ出現率」を追加）
    f_label = font("ja", 26)
    f_star  = font("ja_bold", 30)
    if is_ng:
        d.text((120, 510), "試験出現率",   font=f_label, fill=C["muted"])
        d.text((CARD_W - 120, 510), "☆☆☆☆☆", font=f_star, fill=C["border"], anchor="ra")
        d.text((120, 568), "日常使用率",   font=f_label, fill=C["muted"])
        d.text((CARD_W - 120, 568), "☆☆☆☆☆", font=f_star, fill=C["border"], anchor="ra")
        d.text((120, 626), "ドラマ出現率", font=f_label, fill=C["muted"])
        d.text((CARD_W - 120, 626), "★★★★★", font=f_star, fill=C["gold"],   anchor="ra")
    else:
        d.text((120, 515), "試験出現率", font=f_label, fill=C["muted"])
        d.text((CARD_W - 120, 515), "☆☆☆☆☆", font=f_star, fill=C["border"], anchor="ra")
        d.text((120, 580), "人生重要度", font=f_label, fill=C["muted"])
        d.text((CARD_W - 120, 580), "★★★★★", font=f_star, fill=C["gold"],   anchor="ra")

    hline(d, 685, pad=60)

    # 日本語の意味
    text_center(d, 790, data["japanese_meaning"], font("ja_bold", 46), C["red"])

    # フッター
    d.rectangle([0, CARD_H - 85, CARD_W, CARD_H], fill=C["header"])
    d.text((CARD_W // 2, CARD_H - 42), "▶  スワイプして使い方をチェック", font=font("ja", 24), fill=C["white"], anchor="mm")

    # 二重枠 + コーナー装飾
    d.rectangle([8,  8,  CARD_W - 8,  CARD_H - 8],  outline=C["border"], width=3)
    d.rectangle([16, 16, CARD_W - 16, CARD_H - 16], outline=C["border"], width=1)
    draw_corners(d, margin=30)

    if is_ng:
        img = draw_ng_badge(img)
    return img


def card2(data: dict) -> Image.Image:
    """カード2: 意味・例文・使う場面"""
    img = Image.new("RGB", (CARD_W, CARD_H), C["bg"])
    d   = ImageDraw.Draw(img)
    draw_dot_bg(d)

    d.rectangle([0, 0, CARD_W, 105], fill=C["header"])
    d.text((CARD_W // 2, 52), "意味・使い方", font=font("ja", 32), fill=C["white"], anchor="mm")

    y = 148
    text_center(d, y, data["phrase"], font("en", 36), C["red"])
    y += 58
    hline(d, y)
    y += 42

    d.text((80, y), "[ 意味 ]", font=font("ja", 26), fill=C["muted"])
    y += 44
    text_center(d, y, data["japanese_meaning"], font("ja_bold", 44), C["text"])
    y += 80
    hline(d, y)
    y += 38

    d.text((80, y), "[ 例文 ]", font=font("ja", 26), fill=C["muted"])
    y += 44
    en_lines = wrap(data["example_en"], font("en", 32), CARD_W - 120, d)
    y = text_block(d, y, en_lines, font("en", 32), C["text"], 48)
    y += 10
    ja_lines = wrap(data["example_ja"], font("ja", 28), CARD_W - 120, d)
    y = text_block(d, y, ja_lines, font("ja", 28), C["muted"], 42)
    y += 28

    hline(d, y)
    y += 36
    sit_lines = wrap(data["situation"], font("ja", 28), CARD_W - 120, d)
    text_block(d, y, sit_lines, font("ja", 28), C["text"], 44)

    d.rectangle([0, CARD_H - 85, CARD_W, CARD_H], fill=C["header"])
    d.text((CARD_W // 2, CARD_H - 42), "▶  もう1枚！", font=font("ja", 26), fill=C["white"], anchor="mm")
    d.rectangle([8,  8,  CARD_W - 8,  CARD_H - 8],  outline=C["border"], width=3)
    d.rectangle([16, 16, CARD_W - 16, CARD_H - 16], outline=C["border"], width=1)
    draw_corners(d)
    return img


def card3(data: dict) -> Image.Image:
    """カード3: 語源・豆知識 + フォローCTA"""
    img = Image.new("RGB", (CARD_W, CARD_H), C["bg"])
    d   = ImageDraw.Draw(img)
    draw_dot_bg(d)

    # ヘッダー（琥珀色）
    d.rectangle([0, 0, CARD_W, 105], fill=C["amber"])
    d.text((CARD_W // 2, 52), "語源・豆知識", font=font("ja", 32), fill=C["white"], anchor="mm")

    y = 148
    text_center(d, y, data["phrase"], font("en", 36), C["amber"])
    y += 58
    hline(d, y, color=C["amber"])
    y += 35

    # 語源ボックス
    ety_lines = wrap(data.get("etymology", "---"), font("ja", 30), CARD_W - 180, d)
    box_bot   = y + 30 + len(ety_lines) * 52 + 24
    d.rounded_rectangle([55, y, CARD_W - 55, box_bot], radius=12, fill=C["amber_lt"])
    d.rounded_rectangle([55, y, CARD_W - 55, box_bot], radius=12, outline=C["amber"], width=2)

    # 大きな開きクォート
    d.text((82, y + 12), '"', font=font("en_bold", 60), fill=C["amber"], anchor="lt")

    y += 28
    y = text_block(d, y, ety_lines, font("ja", 30), C["text"], 52)
    y = box_bot + 38

    ornament_line(d, y, color=C["amber"])
    y += 42

    # 豆知識
    fact_lines = wrap(data.get("fun_fact", ""), font("ja", 28), CARD_W - 120, d)
    y = text_block(d, y, fact_lines, font("ja", 28), C["muted"], 46)
    y += 42

    ornament_line(d, y, color=C["border"])
    y += 48

    # フォローCTA
    text_center(d, y, "毎日1フレーズ更新中！", font("ja", 26), C["muted"])
    y += 54
    text_center(d, y, ">> アカウントをフォローしてね", font("ja_bold", 34), C["header"])

    # ハッシュタグ
    tags = "  ".join(f"#{t}" for t in data.get("hashtags", ["英語学習"])[:4])
    d.text((CARD_W // 2, CARD_H - 102), tags, font=font("ja", 20), fill=C["muted"], anchor="mm")

    d.rectangle([0, CARD_H - 85, CARD_W, CARD_H], fill=C["amber"])
    d.text((CARD_W // 2, CARD_H - 42), "絶妙にテストに出ない英単語帳", font=font("ja", 26), fill=C["white"], anchor="mm")
    d.rectangle([8,  8,  CARD_W - 8,  CARD_H - 8],  outline=C["border"], width=3)
    d.rectangle([16, 16, CARD_W - 16, CARD_H - 16], outline=C["border"], width=1)
    draw_corners(d, color=C["amber"])
    return img


def save_cards(data: dict, no: int) -> list[str]:
    slug   = re.sub(r"[^a-z0-9]+", "-", data["phrase"].lower())[:28].strip("-")
    prefix = f"{no:03d}_{slug}"
    paths  = []
    for i, (maker, args) in enumerate(
        [(card1, (data, no)), (card2, (data,)), (card3, (data,))], 1
    ):
        img = maker(*args)
        p   = CARDS_DIR / f"{prefix}_card{i}.png"
        img.save(p, "PNG")
        paths.append(str(p))
    return paths


# ============================================================
# フレーズパーサー
# ============================================================
_SKIP = [
    r"^\d+-\d+$",
    r"^[\d]+$",
    r"^[\s]*$",
    r"^英語フレーズ$",
    r"^モダンファミリー$",
    r"^[ぁ-んァ-ン]{2,}$",
]

def _parse_file(path: Path, is_ng: bool = False) -> tuple[list[dict], set[str]]:
    phrases, seen = [], set()
    if not path.exists():
        return phrases, seen
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or any(re.match(p, line) for p in _SKIP):
                continue
            ng_flag = is_ng
            if line.upper().startswith("[NG]"):
                ng_flag = True
                line = line[4:].strip()
            line = re.sub(r"^[-ー–—]+\s*", "", line).strip()
            if not re.search(r"[a-zA-Z]", line):
                continue
            m       = re.search(r"[぀-ヿ一-鿿]", line)
            phrase  = line[:m.start()].strip() if m else line.strip()
            ja_hint = line[m.start():].strip()  if m else None
            if not phrase or not re.search(r"[a-zA-Z]", phrase):
                continue
            key = phrase.lower()
            if key in seen:
                continue
            seen.add(key)
            phrases.append({"phrase": phrase, "ja_hint": ja_hint, "is_ng": ng_flag})
    return phrases, seen


def parse_phrases(main_path: Path, ng_path: Path = None) -> list[dict]:
    all_phrases, seen = _parse_file(main_path, is_ng=False)
    if ng_path and ng_path.exists():
        ng_phrases, _ = _parse_file(ng_path, is_ng=True)
        for p in ng_phrases:
            if p["phrase"].lower() not in seen:
                seen.add(p["phrase"].lower())
                all_phrases.append(p)
    return all_phrases


# ============================================================
# Claude API コンテンツ生成
# ============================================================
_STD_DESC = "ネイティブが日常で使うカジュアルな表現。試験には出ないが会話に役立つ。"
_NG_DESC  = "日常では絶対使わない単語（法律・医療・専門用語など）。知っていても使えない度MAX。ドラマや映画では頻出。"

PROMPT_TEMPLATE = """\
あなたは「絶妙にテストに出ない英単語帳」というInstagramシリーズのコンテンツライターです。
テーマ: ネイティブが日常で使うのに受験・資格試験には絶対出ない英語フレーズ。
ユーモアと実用性を兼ね備えた内容にしてください。

フレーズ: {phrase}{hint}
カテゴリ: {category_desc}

以下のJSON形式のみ出力してください（他の文は不要）:
{{
  "phrase": "{phrase}",
  "japanese_meaning": "日本語の意味（25字以内、端的に）",
  "example_en": "自然な英語例文（ネイティブが実際に使う文、30語以内）",
  "example_ja": "例文の日本語訳",
  "situation": "こんな場面で使う（40字以内）",
  "etymology": "語源・由来・雑学（面白くユーモアあり、80字以内）",
  "fun_fact": "関連する豆知識や文化的背景（60字以内）",
  "instagram_caption": "Instagram投稿文（絵文字あり・ハッシュタグなし・3文程度）",
  "x_post": "X(Twitter)投稿文（140字以内・絵文字あり・ハッシュタグ2〜3個込み）",
  "hashtags": ["英語", "英語学習", "英語フレーズ", "ネイティブ英語", "テストに出ない英語"]
}}"""


def generate(phrase_data: dict, no: int, client: anthropic.Anthropic) -> dict:
    hint          = f"\n参考の日本語訳: {phrase_data['ja_hint']}" if phrase_data.get("ja_hint") else ""
    is_ng         = phrase_data.get("is_ng", False)
    category_desc = _NG_DESC if is_ng else _STD_DESC
    prompt = PROMPT_TEMPLATE.format(
        phrase=phrase_data["phrase"], hint=hint, category_desc=category_desc
    )
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text
    m    = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"JSON解析失敗:\n{text}")
    data = json.loads(m.group())
    data["post_number"]  = no
    data["is_ng"]        = is_ng
    data["generated_at"] = datetime.now().isoformat()
    return data


# ============================================================
# データ管理
# ============================================================
def load_data() -> list[dict]:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return []


def save_data(records: list[dict]):
    DATA_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
# メイン
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="絶妙にテストに出ない英単語帳 コンテンツ生成")
    parser.add_argument("--count",   type=int, default=1, metavar="N", help="処理件数（デフォルト: 1）")
    parser.add_argument("--all",     action="store_true", help="全件処理")
    parser.add_argument("--list",    action="store_true", help="フレーズ一覧表示")
    parser.add_argument("--preview", type=int, metavar="N", help="N件プレビュー（画像なし）")
    parser.add_argument("--ng",      action="store_true", help="NGシリーズのみ処理")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)
    CARDS_DIR.mkdir(exist_ok=True)

    all_phrases = parse_phrases(PHRASE_FILE, PHRASE_NG_FILE)
    if args.ng:
        all_phrases = [p for p in all_phrases if p.get("is_ng")]

    generated = load_data()
    done_set  = {r["phrase"].lower() for r in generated}
    remaining = [p for p in all_phrases if p["phrase"].lower() not in done_set]

    if args.list:
        print(f"\n{'No':>4}  {'状態':^4}  {'種別':^4}  フレーズ")
        print("-" * 65)
        done_map = {r["phrase"].lower(): r for r in generated}
        for i, p in enumerate(all_phrases, 1):
            status = "✓" if p["phrase"].lower() in done_map else "・"
            kind   = "NG" if p.get("is_ng") else "  "
            hint   = f"  [{p['ja_hint']}]" if p.get("ja_hint") else ""
            print(f"{i:>4}  {status:^4}  {kind:^4}  {p['phrase']}{hint}")
        print(f"\n合計: {len(all_phrases)}件 / 生成済み: {len(done_set)}件 / 残り: {len(remaining)}件")
        return

    print(f"\n合計: {len(all_phrases)}件 | 生成済み: {len(done_set)}件 | 残り: {len(remaining)}件\n")
    if not remaining:
        print("全フレーズの生成が完了しています！")
        return

    if args.preview:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            print("エラー: .env に ANTHROPIC_API_KEY を設定してください")
            sys.exit(1)
        client  = anthropic.Anthropic(api_key=api_key)
        targets = remaining[: args.preview]
        for i, p in enumerate(targets, 1):
            no   = len(generated) + i
            kind = "[NG] " if p.get("is_ng") else ""
            print(f"[{i}/{len(targets)}] {kind}{p['phrase']} ...")
            data = generate(p, no, client)
            print(f"  意味    : {data['japanese_meaning']}")
            print(f"  例文    : {data['example_en']}")
            print(f"  語源    : {data.get('etymology', '')}")
            print(f"  豆知識  : {data.get('fun_fact', '')}")
            print(f"  X投稿   : {data['x_post']}")
            print()
        return

    count   = len(remaining) if args.all else min(args.count, len(remaining))
    targets = remaining[:count]

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("エラー: .env に ANTHROPIC_API_KEY を設定してください")
        sys.exit(1)
    client = anthropic.Anthropic(api_key=api_key)

    for i, p in enumerate(targets, 1):
        no   = len(generated) + 1
        kind = "[NG] " if p.get("is_ng") else ""
        print(f"[{i}/{count}] No.{no:03d}  {kind}{p['phrase']}")
        try:
            data       = generate(p, no, client)
            card_paths = save_cards(data, no)
            data["card_paths"] = card_paths
            generated.append(data)
            save_data(generated)
            print(f"  ✓ 意味: {data['japanese_meaning']}")
            print(f"  ✓ 画像: {[Path(cp).name for cp in card_paths]}")
        except Exception as e:
            print(f"  ✗ エラー: {e}")
            continue

    print(f"\n完了！  output/cards/  と  output/generated.json  を確認してください。")


if __name__ == "__main__":
    main()
