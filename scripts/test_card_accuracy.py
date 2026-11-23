#!/usr/bin/env python3
"""Тест точности распознавания карт против ground truth (живая сессия).

    python scripts/test_card_accuracy.py [--seconds 60]

Играет сессию на нашем столе (DOM-клики калибровки), каждый стабильный кадр
сравнивает шаблонное распознавание с /debug/state. Три барьера честности
пары «кроп<->правда»: стабильность сервера (правда до/после кадра равна),
свежесть DOM (отрисованные карты == правда) и устоявшаяся краска (два
подряд скриншота идентичны — CDP может снять кадр до отрисовки).
Это приёмочный тест глаз из PLAN_EYES.md (карты >= 99%).
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request

from PIL import Image, ImageChops

from pokafer.eyes.browser import TableBrowser
from pokafer.eyes.cards import DOM_CARDS_JS, CardTemplates
from pokafer.eyes.sites import preset_for_domain

POS_ORDER = ["pos_left", "pos_tl", "pos_top", "pos_tr", "pos_right"]


def stable_frame(b: TableBrowser, settle_ms: int = 70) -> Image.Image | None:
    """Кадр с устоявшейся краской: два подряд скриншота идентичны."""
    a = b.screenshot()
    time.sleep(settle_ms / 1000)
    c = b.screenshot()
    if ImageChops.difference(a, c).getbbox() is None:
        return c
    return None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://127.0.0.1:8011")
    p.add_argument("--seconds", type=int, default=60)
    p.add_argument("--bots", type=int, default=5)
    args = p.parse_args()

    vw, vh = 1280, 720
    preset = preset_for_domain(args.url.split("//")[-1])

    templates = CardTemplates("pokafer_local")
    print(f"меток шаблонов: {len(templates.labels())}")

    b = TableBrowser(args.url, viewport=(vw, vh), headless=True)
    time.sleep(1.5)
    for _ in range(args.bots - 1):
        b.execute("document.getElementById('addBotBtn').click();")
        time.sleep(0.3)

    def truth() -> dict:
        return json.loads(urllib.request.urlopen(
            f"{args.url}/debug/state", timeout=3).read())

    ok = bad = 0
    failures: list[tuple] = []
    t0 = time.time()
    while time.time() - t0 < args.seconds:
        ta = truth()
        tb = truth()
        if not (ta.get("hand_no") == tb.get("hand_no")
                and ta.get("board") == tb.get("board")
                and (ta.get("hero") or {}).get("cards") == (tb.get("hero") or {}).get("cards")):
            continue
        dom = b.execute(DOM_CARDS_JS)
        if not (dom["hero"] == (ta.get("hero") or {}).get("cards")
                and dom["board"] == (ta.get("board") or [])):
            continue
        frame = stable_frame(b)
        if frame is None:
            continue
        t = ta
        pairs: list[tuple[Image.Image, str]] = []
        hero_cards = (t.get("hero") or {}).get("cards")
        x, y, w, h = preset.zone("hero.cards", (0, 0, vw, vh))
        if hero_cards and len(hero_cards) == 2:
            half = w // 2
            pairs += [(frame.crop((x, y, x + half, y + h)), hero_cards[0]),
                      (frame.crop((x + half, y, x + w, y + h)), hero_cards[1])]
        board = t.get("board") or []
        bd = preset.zones["board"]
        for i, cs in enumerate(board):
            xc = (bd["center_x"] + (i - (len(board) - 1) / 2) * bd["pitch"]) * vw
            bx, by = int(xc - bd["card_w"] * vw / 2), int(bd["y"] * vh)
            pairs.append((frame.crop((bx, by, bx + int(bd["card_w"] * vw),
                                      by + int(bd["card_h"] * vh))), cs))
        if t.get("hand_over"):
            for i, bot in enumerate((t.get("bots") or [])[:len(POS_ORDER)]):
                cards = bot.get("cards")
                if not cards or len(cards) != 2:
                    continue
                x, y, w, h = preset.zone(f"seat.{POS_ORDER[i]}.cards", (0, 0, vw, vh))
                half = w // 2
                pairs += [(frame.crop((x, y, x + half, y + h)), cards[0]),
                          (frame.crop((x + half, y, x + w, y + h)), cards[1])]
        for img, want in pairs:
            name, frac = templates.match(img)
            if name == want:
                ok += 1
            else:
                bad += 1
                if len(failures) < 10:
                    failures.append((want, name, round(frac, 4)))
        b.execute("""
            const cc = document.getElementById('checkCallBtn');
            const rb = document.getElementById('raiseBtn');
            const nx = document.getElementById('nextHandFloat');
            if (!nx.disabled) { nx.click(); }
            else if (!cc.disabled) {
                if (Math.random() < 0.3 && !rb.disabled) rb.click(); else cc.click();
            }
        """)
        time.sleep(0.3)

    total = ok + bad
    rate = ok / total if total else 0.0
    print(f"распознано {total} карт: верно {ok} ({rate:.1%}), ошибок {bad}")
    if failures:
        print("ошибки (правда -> распознано, доля отличий):", failures)
    b.close()
    raise SystemExit(0 if rate >= 0.99 and total >= 200 else 1)


if __name__ == "__main__":
    main()
