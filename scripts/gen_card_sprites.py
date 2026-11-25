#!/usr/bin/env python3
"""Лица карт веб-стола — растровые спрайты (детерминированный рендер).

    python scripts/gen_card_sprites.py [--font /путь/ЖирныйШрифт.ttf]

Карты реальных румов — картинки. Шрифтовый рендер текста зависит от среды:
headless и видимое окно, видеокарта, шрифты машины дают сотни отличающихся
пикселей на карту (замерено), а спрайт печатается пиксель-в-пиксель везде.
Глаза сравнивают карты попиксельно, поэтому лица карт стола — спрайты.

Выход (генерируется один раз и коммитится — результат не зависит от машины,
где потом открывается стол):
    pokafer/web/static/cards/58x82.png   лист 13x4: ранги 2..A x масти s h d c
    pokafer/web/static/cards/46x66.png   то же для борда (small)
    pokafer/web/static/cards/back.png    рубашка 34x48
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parents[1] / "pokafer" / "web" / "static" / "cards"
RANKS = "23456789TJQKA"
SUITS = "shdc"
GLYPH = {"s": "\u2660", "h": "\u2665", "d": "\u2666", "c": "\u2663"}
FACE = (253, 253, 248)
INK = (27, 31, 39)
RED = (196, 60, 60)
EDGE = tuple(round(c * 0.65) for c in FACE)   # кромка: непрозрачная, без смешения с сукном
SS = 4                                        # суперсэмплинг формы карты
# (ширина, высота, кегль ранга, кегль масти, отступ слева/справа, сверху/снизу)
BIG = (58, 82, 24, 20, 7, 5)
SMALL = (46, 66, 20, 16, 7, 5)


def _default_font() -> str:
    out = subprocess.run(["fc-match", "-f", "%{file}", "system-ui:weight=bold"],
                         capture_output=True, text=True, check=True).stdout
    if not out.endswith((".ttf", ".otf")):
        raise SystemExit("не найден жирный системный шрифт: укажите --font")
    return out


def _shape(w: int, h: int, radius: int) -> Image.Image:
    """Альфа-маска скруглённого прямоугольника с антиалиасингом."""
    big = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, w * SS - 1, h * SS - 1),
                                          radius=radius * SS, fill=255)
    return big.resize((w, h), Image.LANCZOS)


def _baseline(font: ImageFont.FreeTypeFont, line_top: float, line_h: float) -> float:
    """Базовая линия глифа в строке высоты line_h (как в браузере: line-height 1)."""
    ascent, descent = font.getmetrics()
    return line_top + (line_h - (ascent + descent)) / 2 + ascent


def card(w: int, h: int, rank_px: int, suit_px: int, pad_x: int, pad_y: int,
         rank: str, suit: str, font_path: str) -> Image.Image:
    img = Image.new("RGBA", (w, h), FACE + (255,))
    draw = ImageDraw.Draw(img)
    color = RED if suit in "hd" else INK
    rank_font = ImageFont.truetype(font_path, rank_px)
    suit_font = ImageFont.truetype(font_path, suit_px)
    # ранг — сверху слева, масть — снизу справа (как раньше было у CSS-карты)
    draw.text((pad_x, _baseline(rank_font, pad_y, rank_px)), rank,
              font=rank_font, fill=color, anchor="ls")
    advance = suit_font.getlength(GLYPH[suit])
    draw.text((round(w - pad_x - advance), _baseline(suit_font, h - pad_y - suit_px, suit_px)),
              GLYPH[suit], font=suit_font, fill=color, anchor="ls")
    alpha = _shape(w, h, 7)
    px = img.load()
    for y in range(h):
        for x in range(w):
            if _on_edge(x, y, w, h):
                px[x, y] = EDGE + (255,)
    img.putalpha(alpha)
    return img


def _on_edge(x: int, y: int, w: int, h: int) -> bool:
    return x == 0 or y == 0 or x == w - 1 or y == h - 1


def sheet(spec: tuple, font_path: str) -> Image.Image:
    w, h, *_ = spec
    out = Image.new("RGBA", (w * len(RANKS), h * len(SUITS)), (0, 0, 0, 0))
    for row, suit in enumerate(SUITS):
        for col, rank in enumerate(RANKS):
            out.paste(card(*spec, rank, suit, font_path), (col * w, row * h))
    return out


def back() -> Image.Image:
    """Рубашка: диагональные полосы, как у прежней CSS-карты."""
    w, h = 34, 48
    big = Image.new("RGBA", (w * SS, h * SS), (51, 80, 126, 255))
    draw = ImageDraw.Draw(big)
    period = 20 * SS
    for offset in range(-h * SS - w * SS, w * SS + h * SS, period):
        draw.polygon([(offset, 0), (offset + period // 2, 0),
                      (offset + period // 2 + h * SS, h * SS), (offset + h * SS, h * SS)],
                     fill=(42, 66, 102, 255))
    img = big.resize((w, h), Image.LANCZOS)
    img.putalpha(_shape(w, h, 7))
    return img


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--font", default=None, help="жирный шрифт с глифами мастей")
    args = p.parse_args()
    font_path = args.font or _default_font()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, spec in (("58x82", BIG), ("46x66", SMALL)):
        path = OUT_DIR / f"{name}.png"
        sheet(spec, font_path).save(path, optimize=True)
        print(f"{path} ({path.stat().st_size} байт)")
    path = OUT_DIR / "back.png"
    back().save(path, optimize=True)
    print(f"{path} ({path.stat().st_size} байт); шрифт: {font_path}")


if __name__ == "__main__":
    main()
