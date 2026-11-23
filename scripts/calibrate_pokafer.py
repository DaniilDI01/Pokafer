#!/usr/bin/env python3
"""Калибровка пресета pokafer_local: selenium-браузер + DOM-мерки -> TOML.

    python scripts/calibrate_pokafer.py [--url http://127.0.0.1:8011]
        [--viewport 1280x720] [--headless] [--seconds 35]

Процедура: открывает наш стол в управляемом браузере с ТОЧНЫМ viewport,
сажает 5 ботов (все позиции), играет несколько раздач (герой коллирует,
изредка рейзит — чтобы увидеть фишки ставок), собирает bounding-боксы всех
зон по DOM (калибровке DOM разрешён; рантайму глаз — нет) и пишет
data/eyes/pokafer_local.toml.

ВАЖНО: пресет зависит от вёрстки стола — при смене раскладки перезапустить.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

EYES_DIR = Path("data/eyes")


def _fmt(v: float) -> str:
    return f"{v:.4f}"


def _zone_from_box(x0: float, y0: float, x1: float, y1: float,
                   pad: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)):
    """[x0, y0, x1, y1] (доли) -> [x, y, w, h] с подушкой (left, top, right, bottom)."""
    x, y = x0 - pad[0], y0 - pad[1]
    w = (x1 - x0) + pad[0] + pad[2]
    h = (y1 - y0) + pad[1] + pad[3]
    return [round(max(x, 0.0), 4), round(max(y, 0.0), 4), round(w, 4), round(h, 4)]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://127.0.0.1:8011")
    p.add_argument("--viewport", default="1280x720")
    p.add_argument("--headless", action="store_true", default=True)
    p.add_argument("--headed", dest="headless", action="store_false")
    p.add_argument("--seconds", type=int, default=35)
    args = p.parse_args()

    vw, vh = (int(x) for x in args.viewport.split("x"))

    from pokafer.eyes.browser import TableBrowser

    b = TableBrowser(args.url, viewport=(vw, vh), headless=args.headless)
    try:
        # 5 ботов — все позиции мест
        b.execute("""
            const sleep = (m) => new Promise((r) => setTimeout(r, m));
            (async () => {
              for (let i = 0; i < 4; i++) {
                document.getElementById('addBotBtn').click(); await sleep(300);
              }
            })();
        """)
        time.sleep(2.0)

        obs: dict[str, list[float]] = {}
        board_frames: list[list] = []

        def take(key: str, r) -> None:
            box = obs.setdefault(key, [1e9, 1e9, 0.0, 0.0])
            box[0] = min(box[0], r["x"] / vw)
            box[1] = min(box[1], r["y"] / vh)
            box[2] = max(box[2], (r["x"] + r["width"]) / vw)
            box[3] = max(box[3], (r["y"] + r["height"]) / vh)

        t0 = time.time()
        _debug_printed = False
        while time.time() - t0 < args.seconds:
            state = b.execute("""
                const vw = innerWidth, vh = innerHeight;
                const box = (r) => ({x: r.x, y: r.y, width: r.width, height: r.height});
                const out = {seats: {}, board_cards: [], hero_bet: null, result: null};
                for (const s of document.querySelectorAll('.seat.villain')) {
                  const pos = [...s.classList].find((c) => c.startsWith('pos-'));
                  const cards = s.querySelector('.cards');
                  if (cards.querySelectorAll('.card').length > 0)
                    out.seats['cards_' + pos] = box(cards.getBoundingClientRect());
                  const chip = s.querySelector('.bet-chip');
                  if (chip && chip.offsetWidth > 0)
                    out.seats['bet_' + pos] = box(chip.getBoundingClientRect());
                  out.seats['stack_' + pos] = box(s.querySelector('.stack').getBoundingClientRect());
                }
                const hb = document.getElementById('hBet');
                if (hb && hb.offsetWidth > 0) out.hero_bet = box(hb.getBoundingClientRect());
                out.hero_cards = box(document.querySelector('.seat.hero .cards').getBoundingClientRect());
                out.hero_stack = box(document.querySelector('.seat.hero .stack').getBoundingClientRect());
                out.pot = box(document.getElementById('pot').getBoundingClientRect());
                out.street = box(document.getElementById('street').getBoundingClientRect());
                out.board_n = document.querySelectorAll('#board .card').length;
                for (const c of document.querySelectorAll('#board .card'))
                  out.board_cards.push(box(c.getBoundingClientRect()));
                const rs = document.getElementById('resultStrip');
                if (rs && rs.offsetWidth > 0) out.result = box(rs.getBoundingClientRect());
                return out;
            """)
            if not _debug_printed:
                print("DEBUG первый срез: n_board =", state["board_cards"] and len(state["board_cards"]),
                      "| ключи seats:", sorted(state["seats"].keys())[:8] or "ПУСТО")
                _debug_printed = True
            for key, r in state["seats"].items():
                # JS-классы: pos-top (дефис); ключи пресета: pos_top (подчёркивание)
                take(key.replace("pos-", "pos_"), r)

            if state.get("hero_bet"):
                take("hero_bet", state["hero_bet"])
            take("hero_cards", state["hero_cards"])
            take("hero_stack", state["hero_stack"])
            take("pot", state["pot"])
            take("street", state["street"])
            if state.get("result"):
                take("result", state["result"])
            if state["board_cards"]:
                board_frames.append(state["board_cards"])

            # герой: колл/чек, изредка мин-рейз (фишка героя в кадре)
            act = b.execute("""
                const st = document.getElementById('status').textContent;
                const cc = document.getElementById('checkCallBtn');
                const rb = document.getElementById('raiseBtn');
                const nx = document.getElementById('nextHandFloat');
                if (st.includes('рука завершена')) {
                  if (nx && !nx.disabled) { nx.click(); return 'next'; }
                } else if (st.startsWith('ваш ход') && cc && !cc.disabled) {
                  if (Math.random() < 0.35 && rb && !rb.disabled) { rb.click(); return 'raise'; }
                  cc.click(); return 'call';
                }
                return 'wait';
            """)
            _ = act
            time.sleep(0.3)

        # ---- сборка TOML ----
        pos_names = ("pos_top", "pos_tl", "pos_tr", "pos_left", "pos_right")
        lines = [
            "# Калибровка пресета pokafer_local — СГЕНЕРИРОВАНО",
            f"# scripts/calibrate_pokafer.py, {time.strftime('%Y-%m-%d %H:%M')}, "
            f"viewport {vw}x{vh}. Зоны — доли окна [x, y, w, h].",
            "[meta]",
            f"viewport_ref = [{vw}, {vh}]",
            "",
            "[hero]",
        ]
        z = obs["hero_cards"]
        lines.append(f"cards = [{_fmt(z[0])}, {_fmt(z[1])}, "
                     f"{_fmt(z[2] - z[0])}, {_fmt(z[3] - z[1])}]")
        z = obs["hero_stack"]
        lines.append(f"stack = [{_fmt(z[0] - 0.008)}, {_fmt(z[1] - 0.006)}, "
                     f"{_fmt(z[2] - z[0] + 0.016)}, {_fmt(z[3] - z[1] + 0.012)}]")
        if "hero_bet" in obs:
            z = obs["hero_bet"]
            lines.append(f"bet = [{_fmt(z[0] - 0.004)}, {_fmt(z[1] - 0.005)}, "
                         f"{_fmt(z[2] - z[0] + 0.008)}, {_fmt(z[3] - z[1] + 0.012)}]")
        else:
            lines.append("# bet: не наблюдена — доиграть раздачу с рейзом и перекалибровать")

        # борд: центр/шаг из кадра с максимумом карт (все слоты одного кадра)
        if board_frames:
            frame = max(board_frames, key=len)
            centers = [(r["x"] + r["width"] / 2) / vw for r in frame]
            card_w = sum(r["width"] for r in frame) / len(frame) / vw
            card_h = sum(r["height"] for r in frame) / len(frame) / vh
            y0 = sum(r["y"] for r in frame) / len(frame) / vh
            step = (centers[-1] - centers[0]) / (len(frame) - 1) if len(frame) > 1 else 0.0
            center_x = sum(centers) / len(centers)
            lines += [
                "",
                "# борд: слоты считаются от центра (n карт по pitch)",
                "[board]",
                f"center_x = {_fmt(center_x)}",
                f"y        = {_fmt(y0)}",
                f"card_w   = {_fmt(card_w)}",
                f"card_h   = {_fmt(card_h)}",
                f"pitch    = {_fmt(step)}",
            ]
        else:
            lines += ["", "# board: карты не наблюдались", "[board]"]

        for key, pads in (("pot", (0.04, 0.007, 0.22, 0.015)),
                          ("street", (0.03, 0.006, 0.30, 0.013)),
                          ("result", (0.02, 0.008, 0.02, 0.012))):
            if key in obs:
                z = obs[key]
                lines += ["", f"[{key}]",
                          f"area = [{_fmt(z[0] - pads[0])}, {_fmt(z[1] - pads[1])}, "
                          f"{_fmt(z[2] - z[0] + pads[0] + pads[2])}, {_fmt(z[3] - z[1] + pads[1] + pads[3])}]"]
            else:
                lines += ["", f"[{key}]", "# не наблюдена"]

        for pos in pos_names:
            lines += ["", f"[seat.{pos}]"]
            if f"cards_{pos}" in obs:
                z = obs[f"cards_{pos}"]
                cy = (z[1] + z[3]) / 2
                lines.append(f"cards = [{_fmt(z[0] - 0.004)}, {_fmt(cy - 0.059)}, "
                             f"{_fmt(z[2] - z[0] + 0.047)}, {_fmt(0.118)}]")
            else:
                lines.append(f"cards = [0, 0, 0, 0]  # не наблюдена — доиграть и перекалибровать")
            if f"stack_{pos}" in obs:
                z = obs[f"stack_{pos}"]
                lines.append(f"stack = [{_fmt(z[0] - 0.008)}, {_fmt(z[1] - 0.006)}, "
                             f"{_fmt(z[2] - z[0] + 0.016)}, {_fmt(z[3] - z[1] + 0.012)}]")
            if f"bet_{pos}" in obs:
                z = obs[f"bet_{pos}"]
                lines.append(f"bet = [{_fmt(z[0] - 0.004)}, {_fmt(z[1] - 0.005)}, "
                             f"{_fmt(z[2] - z[0] + 0.008)}, {_fmt(z[3] - z[1] + 0.012)}]")

        lines += ["", "[act]"]
        act_zones = b.execute("""
            const vw = innerWidth, vh = innerHeight;
            const pct = (e) => { const r = e.getBoundingClientRect();
              return [+(r.x/vw).toFixed(4), +(r.y/vh).toFixed(4), +(r.width/vw).toFixed(4), +(r.height/vh).toFixed(4)]; };
            return {
              fold: pct(document.getElementById('foldBtn')),
              check: pct(document.getElementById('checkCallBtn')),
              raise: pct(document.getElementById('raiseBtn')),
              slider: pct(document.getElementById('raiseSlider')),
              input: pct(document.getElementById('raiseInput')),
              preset_min: pct(document.querySelector('.presets button:nth-child(1)')),
              preset_half: pct(document.querySelector('.presets button:nth-child(2)')),
              preset_pot: pct(document.querySelector('.presets button:nth-child(3)')),
              preset_max: pct(document.querySelector('.presets button:nth-child(4)')),
              next: pct(document.getElementById('nextHandFloat')),
            };
        """)
        for key, val in act_zones.items():
            lines.append(f"{key} = {val}")

        out = EYES_DIR / "pokafer_local.toml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"пресет записан: {out}")
    finally:
        b.close()


if __name__ == "__main__":
    main()
