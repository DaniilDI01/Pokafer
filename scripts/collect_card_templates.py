#!/usr/bin/env python3
"""Сбор шаблонов карт для пресета pokafer_local: selenium + ground truth.

    python scripts/collect_card_templates.py [--url ...] [--seconds 90]

Глаза играют сессию (герой коллирует/рейзит через DOM-клики калибровки),
каждый кадр: кропы карт по зонам пресета + правда из /debug/state →
копим кропы по меткам (52 карты) → строим шаблоны рангов/мастей
(глиф = левая часть карты: ранг сверху, масть под ним).

Для ЧУЖИХ сайтов правды нет: тот же сборщик без ground truth кластеризует
кропы, а кластеры размечает человек один раз (см. PLAN_EYES.md).
"""
from __future__ import annotations

import argparse
import time
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image

TEMPLATES_DIR = Path("data/eyes/templates")

def stable_frame(b, settle_ms: int = 70):
    """Кадр с устоявшейся краской: два подряд скриншота идентичны.

    CDP-скриншот может снять кадр ДО отрисовки обновления DOM — пиксельная
    стабильность гарантирует, что захвачено смонтированное состояние.
    """
    from PIL import ImageChops
    a = b.screenshot()
    import time as _t
    _t.sleep(settle_ms / 1000)
    c = b.screenshot()
    if ImageChops.difference(a, c).getbbox() is None:
        return c
    return None

# регионы глифов внутри кропа карты (доли): наш рендер — ранг сверху слева,
# символ масти под ним

RANK_BOX = (0.10, 0.05, 0.55, 0.45)
SUIT_BOX = (0.45, 0.55, 0.98, 0.97)  # символ масти — внизу СПРАВА (align-self: flex-end)


def _glyphs(card: Image.Image):
    """Глифы ранга и масти из кропа карты."""
    w, h = card.size
    rank = card.crop((int(RANK_BOX[0] * w), int(RANK_BOX[1] * h),
                      int(RANK_BOX[2] * w), int(RANK_BOX[3] * h)))
    suit = card.crop((int(SUIT_BOX[0] * w), int(SUIT_BOX[1] * h),
                      int(SUIT_BOX[2] * w), int(SUIT_BOX[3] * h)))
    return rank, suit


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://127.0.0.1:8011")
    p.add_argument("--seconds", type=int, default=90)
    p.add_argument("--bots", type=int, default=5)
    p.add_argument("--preset", default="pokafer_local")
    args = p.parse_args()

    import json
    import urllib.request

    from pokafer.eyes.browser import TableBrowser
    from pokafer.eyes.cards import DOM_CARDS_JS
    from pokafer.eyes.sites import preset_for_domain

    domain = args.url.split("//")[-1]
    preset = preset_for_domain(domain)
    vw, vh = 1280, 720

    b = TableBrowser(args.url, viewport=(vw, vh), headless=True)
    time.sleep(1.5)
    for _ in range(args.bots - 1):
        b.execute("document.getElementById('addBotBtn').click();")
        time.sleep(0.3)

    # позиции мест нашего стола -> порядок ботов из /debug/state
    pos_order = {5: ["pos_left", "pos_tl", "pos_top", "pos_tr", "pos_right"],
                 4: ["pos_left", "pos_tl", "pos_tr", "pos_right"],
                 3: ["pos_left", "pos_top", "pos_right"],
                 2: ["pos_tl", "pos_tr"],
                 1: ["pos_top"]}[args.bots]

    crops: dict[str, list[Image.Image]] = defaultdict(list)  # "As" -> [кропы]
    frames = 0
    showdowns = 0
    stat = {"stable": 0, "unstable": 0, "render_lag": 0, "paint_lag": 0}
    t0 = time.time()
    while time.time() - t0 < args.seconds:
        # защита от состязания: правда до и после кадра должна быть идентична
        # (боты играют между кадрами — иначе метки перемешиваются)
        def _truth():
            return json.loads(
                urllib.request.urlopen(f"{args.url}/debug/state", timeout=2).read()
            )
        ta = _truth()  # правда ДО кадра
        tb = _truth()  # и ПОСЛЕ кадра: изменилось — кадр отбрасывается
        stable = (ta.get("hand_no") == tb.get("hand_no")
                  and ta.get("board") == tb.get("board")
                  and (ta.get("hero") or {}).get("cards") == (tb.get("hero") or {}).get("cards")
                  and ta.get("hand_over") == tb.get("hand_over"))
        if not stable:
            stat["unstable"] += 1
            continue
        stat["stable"] += 1
        truth = ta
        # СВЕЖЕСТЬ РЕНДЕРА: сервер может опережать браузер (WS-латентность) —
        # сверяем отрисованные карты с правдой, иначе метки привяжутся к
        # предыдущему кадру. Коллектору DOM можно (калибровка, не рантайм).
        dom = b.execute(DOM_CARDS_JS)
        if not (dom["hero"] == (truth.get("hero") or {}).get("cards")
                and dom["board"] == (truth.get("board") or [])):
            stat["render_lag"] += 1
            continue
        # краска должна устояться: скриншот не старше DOM (CDP кадр-лаг)
        settled = stable_frame(b)
        if settled is None:
            stat["paint_lag"] += 1
            continue
        frame = settled
        pairs: list[tuple[Image.Image, str]] = []

        hero_cards = (truth.get("hero") or {}).get("cards")
        zone = preset.zone("hero.cards", (0, 0, vw, vh))
        if hero_cards and len(hero_cards) == 2:
            x, y, w, h = zone
            half = w // 2
            pairs.append((frame.crop((x, y, x + half, y + h)), hero_cards[0]))
            pairs.append((frame.crop((x + half, y, x + w, y + h)), hero_cards[1]))

        board = truth.get("board") or []
        if board:
            cx, by, cw, ch, pitch = (
                preset.zones["board"]["center_x"], preset.zones["board"]["y"],
                preset.zones["board"]["card_w"], preset.zones["board"]["card_h"],
                preset.zones["board"]["pitch"],
            )
            for i, card_str in enumerate(board):
                xc = (cx + (i - (len(board) - 1) / 2) * pitch) * vw
                bx, byp = int(xc - cw * vw / 2), int(by * vh)
                pairs.append((frame.crop((bx, byp, bx + int(cw * vw), byp + int(ch * vh))),
                              card_str))

        # вскрытые карты ботов только на шоудауне
        if truth.get("hand_over"):
            showdowns += 1
            bots = truth.get("bots") or []
            for i, bot in enumerate(bots):
                if i >= len(pos_order):
                    break
                cards = bot.get("cards")
                if not cards or len(cards) != 2:
                    continue
                zone = preset.zone(f"seat.{pos_order[i]}.cards", (0, 0, vw, vh))
                x, y, w, h = zone
                half = w // 2
                pairs.append((frame.crop((x, y, x + half, y + h)), cards[0]))
                pairs.append((frame.crop((x + half, y, x + w, y + h)), cards[1]))

        for img, label in pairs:
            crops[label].append(img)
        frames += 1
        if frames % 20 == 0:
            status = b.execute("return document.getElementById('status').textContent.slice(0, 30);")
            print(f"  [кадр {frames}] рука {truth.get('hand_no')} over={truth.get('hand_over')} "
                  f"board={len(truth.get('board') or [])} статус='{status}'")

        # герой пассивно + изредка рейз (чтобы руки доигрывались)
        b.execute("""
            const cc = document.getElementById('checkCallBtn');
            const rb = document.getElementById('raiseBtn');
            const nx = document.getElementById('nextHandFloat');
            // истина — состояние кнопок, не текст статуса (статус может
            // застрять «ошибка ввода» после отклонённого действия)
            if (!nx.disabled) { nx.click(); }
            else if (!cc.disabled) {
                if (Math.random() < 0.3 && !rb.disabled) rb.click(); else cc.click();
            }
        """)
        time.sleep(0.3)

    # ---- шаблоны: самый частый кроп каждой метки ----
    out_dir = TEMPLATES_DIR / args.preset
    out_dir.mkdir(parents=True, exist_ok=True)

    def representative(imgs: list[Image.Image]) -> Image.Image:
        """Самый частый пиксельный хеш = типичный рендер."""
        hashes = Counter(im.tobytes() for im in imgs)
        common = hashes.most_common(1)[0][0]
        return next(im for im in imgs if im.tobytes() == common)

    saved = 0
    for label, imgs in sorted(crops.items()):
        if len(imgs) < 2:
            continue  # мало наблюдений — шаблон ненадёжен
        # шаблон на каждый встреченный размер кропа (большие/бордовые карты
        # рендерятся с разным антиалиасингом — сравниваются только в своем классе)
        by_size: dict[tuple, list[Image.Image]] = {}
        for im in imgs:
            by_size.setdefault(im.size, []).append(im)
        for size, group in sorted(by_size.items()):
            if len(group) < 2:
                continue
            rep = representative(group)
            # размер в имени: классы не перезаписывают друг друга
            rep.save(out_dir / f"card_{label}_{size[0]}x{size[1]}.png")
            saved += 1

    print(f"кадров: {frames}, шоудаунов: {showdowns}, стабильных: {stat['stable']}, "
          f"нестабильных: {stat['unstable']}, отставаний рендера: {stat['render_lag']}, "
          f"отставаний краски: {stat['paint_lag']}, "
          f"меток: {len(crops)}, "
          f"шаблонов карт: {saved} -> {out_dir}")
    suits = Counter(lbl[1] for lbl in crops)
    ranks = Counter(lbl[0] for lbl in crops)
    print("ранги:", dict(sorted(ranks.items())))
    print("масти:", dict(sorted(suits.items())))
    b.close()


if __name__ == "__main__":
    main()
