#!/usr/bin/env python3
"""Калибровка пресета pokafer_local: слоты карт и зоны чисел.

    python scripts/calibrate_pokafer.py [--url http://127.0.0.1:8011]
        [--viewport 1280x720]

Стол открывается в управляемом браузере с ТОЧНЫМ viewport (5 ботов — все
места), игра замораживается на ходе героя, и калибровка читает по DOM, ГДЕ
лежат элементы (калибровке DOM разрешён, рантайму глаз — нет).

Карты. Шаблоны снимаются ПО МЕСТУ: в те же слоты, что и в игре, инъецируется
каждая из 52 карт — кроп совпадает с игрой пиксель-в-пиксель. Детерминизм
проверяется, а не предполагается: пробная карта в каждом слоте обязана
совпасть с шаблоном своего класса размеров (метрикой читателя), все рубашки —
друг с другом, позиции — быть целыми. Иначе калибровка падает с объяснением.

Числа. Готовому распознавателю текста калибровка под шрифт не нужна — только
геометрия: где лежат сами числа (без подписей рядом) и каков фон зоны, чтобы
отличать «элемент скрыт» от «число есть». Самопроверка: строки, выведенные по
месту, читатель чисел обязан прочитать точно.

Одна калибровка = один calibration_id (хеш зон и шаблонов карт):
  data/eyes/pokafer_local.toml                      — пресет
  data/eyes/templates/pokafer_local/*.png           — карты, рубашка, пустые слоты
  data/eyes/templates/pokafer_local/calibration_id

ВАЖНО: пресет зависит от вёрстки стола — при смене раскладки перезапустить
(читатели отказываются работать с шаблонами чужой калибровки).
"""
from __future__ import annotations

import argparse
import hashlib
import time
from pathlib import Path

from PIL import Image

from pokafer.eyes.cards import CardReader, diff_fraction
from pokafer.eyes.numbers import NumberReader
from pokafer.eyes.sites import EYES_DIR, Rect, load_preset
from pokafer.eyes.truth import TableTruthFeed, measure_number_zones, zone_backgrounds

PRESET = "pokafer_local"
LABELS = [r + s for r in "23456789TJQKA" for s in "shdc"]
PROBE = "As"

ACT_SELECTORS = {
    "fold": "#foldBtn", "check": "#checkCallBtn", "raise": "#raiseBtn",
    "slider": "#raiseSlider", "input": "#raiseInput",
    "preset_min": ".presets button:nth-child(1)",
    "preset_half": ".presets button:nth-child(2)",
    "preset_pot": ".presets button:nth-child(3)",
    "preset_max": ".presets button:nth-child(4)",
    "next": "#nextHandFloat",
}
PARK: Rect = (1270, 708, 4, 4)   # пустой край строки статуса: не кнопка, подсветки нет
SEATS_JS = ("return [...document.querySelectorAll('.seat.villain')].map((s) => "
            "[...s.classList].find((c) => c.startsWith('pos-')).replace('pos-', 'pos_'));")
# какие позиции занимают боты по их числу — раскладка самой страницы
LAYOUTS_JS = ("return Object.keys(LAYOUTS).sort((a, b) => a - b)"
              ".map((n) => LAYOUTS[+n].map((c) => c.replace('pos-', 'pos_')));")
UNFOLD_JS = ("document.querySelectorAll('.seat.villain.folded')"
             ".forEach((s) => s.classList.remove('folded'));")
SET_TEXT_JS = ("const el = document.querySelector(arguments[0]); el.style.display = ''; "
               "el.textContent = arguments[1];")
HIDE_JS = ("const el = document.querySelector(arguments[0]); el.textContent = ''; "
           "el.style.display = 'none';")


def _fail(msg: str) -> None:
    raise SystemExit(f"КАЛИБРОВКА СЛОМАЛАСЬ: {msg}")


def _px(name: str, rect: list[float], exact: bool = True) -> Rect:
    """DOM-прямоугольник -> целые пиксели.

    exact: дробная позиция = недетерминизм (вёрстка зависит от содержимого) —
    для слотов карт и текстовых зон это ошибка. Кликабельным кнопкам
    достаточно округления: цель клика — центр.
    """
    if exact and any(abs(v - round(v)) > 0.01 for v in rect):
        _fail(f"{name} на дробной позиции {rect} — вёрстка стола недетерминирована")
    x, y, w, h = (round(v) for v in rect)
    return (x, y, max(w, 1), max(h, 1))


def _crop(image: Image.Image, rect: Rect) -> Image.Image:
    x, y, w, h = rect
    return image.crop((x, y, x + w, y + h))


def _card_name(label: str, crop: Image.Image) -> str:
    return f"card_{label}_{crop.width}x{crop.height}.png"


def _one(feed: TableTruthFeed, selectors: dict[str, str]) -> dict[str, list[float]]:
    """По одному элементу на селектор: иначе раскладка не та, что ожидаем."""
    found = feed.rects(selectors)
    for name, items in found.items():
        if len(items) != 1:
            _fail(f"зона {name}: элементов {len(items)}, ожидался один")
    return {name: items[0] for name, items in found.items()}


# ---------- карты ----------

def _measure_cards(feed: TableTruthFeed, seats: list[str]) -> tuple[dict, dict]:
    """Слоты карт и прочие зоны по DOM (карты мест — с инъекцией пробных)."""
    slots: dict[str, list[Rect]] = {}
    hero = feed.rects({"card": ".seat.hero .cards .card"})["card"]
    if len(hero) != 2:
        _fail("у героя нет двух карт — стол заморожен не на ходе героя")
    slots["hero.cards"] = [_px("hero.cards", r) for r in hero]
    slots["board.slots"] = [_px("board.slots", r)
                            for r in feed.rects({"slot": "#board .slot"})["slot"]]
    for pos in seats:
        cls = pos.replace("_", "-")
        feed.set_seat_cards(pos, [PROBE, PROBE])
        cards = feed.rects({"c": f".seat.{cls} .cards .card"})["c"]
        feed.set_seat_cards(pos, "backs")
        backs = feed.rects({"c": f".seat.{cls} .cards .card"})["c"]
        feed.set_seat_cards(pos, None)
        if len(cards) != 2 or len(backs) != 2:
            _fail(f"у места {pos} не две карты")
        slots[f"seat.{pos}.cards"] = [_px(f"seat.{pos}.cards", r) for r in cards]
        slots[f"seat.{pos}.backs"] = [_px(f"seat.{pos}.backs", r) for r in backs]

    selectors = {"hero.name": ".seat.hero .name", "pot.area": "#pot", "street.area": "#street",
                 "result.area": "#resultStrip", **ACT_SELECTORS}
    for pos in seats:
        selectors[f"seat.{pos}.name"] = f".seat.{pos.replace('_', '-')} .name"
    return slots, {name: _px(name, rect, exact=name not in ACT_SELECTORS)
                   for name, rect in _one(feed, selectors).items()}


def _calibrate_cards(feed: TableTruthFeed, seats: list[str], slots: dict) -> dict[str, Image.Image]:
    """52 карты по месту в двух классах размеров + пробы детерминизма + пустые и рубашки."""
    saved: dict[str, Image.Image] = {}

    def expect(hero: list[str], board: list[str],
               seat: str | None = None, cards: list[str] | None = None) -> dict:
        """Какие карты ДОЛЖНЫ быть отрисованы (прочие места пусты)."""
        dom = {"hero": list(hero), "board": list(board)}
        dom.update({s: list(cards) if s == seat else [] for s in seats})
        return dom

    def shot(want: dict, what: str) -> Image.Image:
        dom = feed.dom_cards()
        if dom != want:
            diff = {k: (dom.get(k), want.get(k)) for k in set(dom) | set(want)
                    if dom.get(k) != want.get(k)}
            _fail(f"страница перерисовалась на съёмке «{what}» — стол не был заморожен "
                  f"(DOM != ожидания: {diff})")
        image = feed.settled()
        if image is None:
            _fail(f"краска не устоялась на съёмке «{what}»")
        return image

    for label in LABELS:
        feed.set_hero_cards([label, label])
        feed.set_board([])
        crop = _crop(shot(expect([label, label], []), label), slots["hero.cards"][0])
        saved[_card_name(label, crop)] = crop
        feed.set_board([label] * 5)
        crop = _crop(shot(expect([label, label], [label] * 5), label), slots["board.slots"][0])
        saved[_card_name(label, crop)] = crop

    def probe(what: str, rect: Rect, setup, want: dict) -> None:
        """Пробная карта в слоте == шаблон её класса размеров (метрикой читателя)."""
        setup()
        crop = _crop(shot(want, what), rect)
        tpl = saved.get(_card_name(PROBE, crop))
        if tpl is None or diff_fraction(crop, tpl) != 0.0:
            _fail(f"рендер слота {what} {rect} отличается от шаблона — вёрстка "
                  "стола недетерминирована")

    feed.set_hero_cards([PROBE, PROBE])
    feed.set_board([])
    probe("hero.cards[1]", slots["hero.cards"][1], lambda: None, expect([PROBE, PROBE], []))
    for i, rect in enumerate(slots["board.slots"][1:], start=1):
        probe(f"board.slots[{i}]", rect, lambda: feed.set_board([PROBE] * 5),
              expect([PROBE, PROBE], [PROBE] * 5))
    feed.set_board([])
    for pos in seats:
        for i, rect in enumerate(slots[f"seat.{pos}.cards"]):
            probe(f"seat.{pos}.cards[{i}]", rect,
                  lambda pos=pos: feed.set_seat_cards(pos, [PROBE, PROBE]),
                  expect([PROBE, PROBE], [], pos, [PROBE, PROBE]))
        feed.set_seat_cards(pos, None)

    image = shot(expect([PROBE, PROBE], []), "пустой борд")
    for i, rect in enumerate(slots["board.slots"]):
        saved[f"empty_board.slots_{i}.png"] = _crop(image, rect)
    back_ref: Image.Image | None = None
    for pos in seats:
        feed.set_seat_cards(pos, None)
        image = shot(expect([PROBE, PROBE], []), f"пустое место {pos}")
        for kind in ("cards", "backs"):
            saved[f"empty_seat.{pos}.{kind}_0.png"] = _crop(image, slots[f"seat.{pos}.{kind}"][0])
        feed.set_seat_cards(pos, "backs")
        image = shot(expect([PROBE, PROBE], [], pos, ["back", "back"]), f"рубашки {pos}")
        for i, rect in enumerate(slots[f"seat.{pos}.backs"]):
            crop = _crop(image, rect)
            if back_ref is None:
                back_ref = crop
                saved[f"back_{crop.width}x{crop.height}.png"] = crop
            elif diff_fraction(crop, back_ref) != 0.0:
                _fail(f"рубашка слота seat.{pos}.backs[{i}] отличается от других — "
                      "вёрстка стола недетерминирована")
        feed.set_seat_cards(pos, None)
    return saved


# ---------- числа ----------

def _selftest_numbers(feed: TableTruthFeed, seat: str) -> None:
    """Строки, выведенные по месту, читатель чисел обязан прочитать точно;
    скрытый элемент — не дать числа. Собирает все ошибки сразу."""
    reader = NumberReader(load_preset(PRESET))
    bad: list[str] = []

    def frame(what: str) -> Image.Image:
        image = feed.settled()
        if image is None:
            _fail(f"самопроверка чисел ({what}): краска не устоялась")
        return image

    feed.js("document.querySelector('#checkCallBtn .label').textContent = 'Колл';")
    for selector, path, texts in (
            ("#hAmount", "hero.stack", ["0", "199.75", "1234.5", "5"]),
            (f".seat.{seat.replace('_', '-')} .stack .amt", f"seat.{seat}.stack", ["42", "0.5"]),
            ("#potAmount", "pot.amount", ["0", "3.5", "199.75", "1500"]),
            ("#callAmount", "act.call_amount", ["0.5", "14.25", "199.75"]),
            ("#hBet", "hero.bet", ["0.5", "12.5"])):
        for text in texts:
            feed.js(SET_TEXT_JS, selector, text)
            got = reader.read(frame(path), path)
            if got.state != "number" or got.text != text:
                bad.append(f"{path}: выведено {text!r}, прочитано {got.state}/{got.text!r} "
                           f"(уверенность {got.score:.2f})")
    for selector, path, want in (("#callAmount", "act.call_amount", "none"),
                                 ("#hBet", "hero.bet", "empty")):
        feed.js(HIDE_JS, selector)
        got = reader.read(frame(path), path)
        if got.state != want:
            bad.append(f"{path}: элемент без числа, прочитано {got.state}/{got.text!r}, "
                       f"ожидалось {want}")
    if bad:
        _fail("самопроверка чисел:\n  " + "\n  ".join(bad))


# ---------- результат ----------

def _write_preset(viewport: tuple[int, int], slots: dict, rects: dict, backgrounds: dict[str, float],
                  seat_layouts: list[list[str]], cards: dict[str, Image.Image]) -> str:
    """TOML пресета; возвращает calibration_id (хеш зон и шаблонов карт)."""
    vw, vh = viewport

    def zone(r: Rect, pad: int = 0) -> str:
        x, y, w, h = r
        return (f"[{(x - pad) / vw:.6f}, {(y - pad) / vh:.6f}, "
                f"{(w + 2 * pad) / vw:.6f}, {(h + 2 * pad) / vh:.6f}]")

    hashed = [(f"{path}[{i}]", r) for path, rs in slots.items() for i, r in enumerate(rs)]
    hashed += [(name, r, 2 if name.endswith(".name") else 0) for name, r in rects.items()]
    hashed += [("tpl", name, hashlib.md5(img.tobytes()).hexdigest())
               for name, img in cards.items()]  # иной рендер карт или спрайты — иная калибровка
    calib_id = hashlib.sha1(repr(sorted(hashed)).encode()).hexdigest()[:12]

    seat_of = sorted({path.split(".")[1] for path in slots if path.startswith("seat.")})
    lines = [
        "# Калибровка пресета pokafer_local — СГЕНЕРИРОВАНО",
        f"# scripts/calibrate_pokafer.py, {time.strftime('%Y-%m-%d %H:%M')}: зоны по DOM,",
        "# шаблоны карт сняты по месту в тех же слотах, что в игре.",
        "# Зоны — доли референс-вьюпорта (coords = ref_px: стол не масштабируется).",
        "[meta]",
        f"viewport_ref = [{vw}, {vh}]",
        'coords = "ref_px"',
        f'calibration_id = "{calib_id}"',
        "# какие позиции занимают боты по их числу (порядок — как в виде стола)",
        "seat_layouts = " + repr(seat_layouts).replace("'", '"'),
        "",
        "[hero]",
        "cards = [" + ", ".join(zone(r) for r in slots["hero.cards"]) + "]",
        f"stack = {zone(rects['hero.stack'])}",     # зоны чисел: только само число
        f"name = {zone(rects['hero.name'], 2)}",
        f"bet = {zone(rects['hero.bet'])}",
        "",
        "[board]",
        "slots = [" + ", ".join(zone(r) for r in slots["board.slots"]) + "]",
        "",
        "[pot]",
        f"area = {zone(rects['pot.area'])}",
        f"amount = {zone(rects['pot.amount'])}",
        "",
        "[street]",
        f"area = {zone(rects['street.area'])}",
        "",
        "[result]",
        f"area = {zone(rects['result.area'])}",
        "",
        "[act]",
    ]
    lines += [f"{k} = {zone(rects[k])}" for k in ACT_SELECTORS]
    lines += [f"call_amount = {zone(rects['act.call_amount'])}",  # число в кнопке «Колл N»
              f"park = {zone(PARK)}  # куда актатор уводит курсор после клика: под курсором "
              "кнопка подсвечена и её число не читается"]
    for pos in seat_of:
        lines += ["", f"[seat.{pos}]",
                  "cards = [" + ", ".join(zone(r) for r in slots[f"seat.{pos}.cards"]) + "]",
                  "backs = [" + ", ".join(zone(r) for r in slots[f"seat.{pos}.backs"]) + "]",
                  f"stack = {zone(rects[f'seat.{pos}.stack'])}",
                  f"name = {zone(rects[f'seat.{pos}.name'], 2)}",
                  f"bet = {zone(rects[f'seat.{pos}.bet'])}"]
    lines += ["", "# зона числа -> яркость фона зоны: фон не тот — элемент скрыт (фишка без ставки)",
              "[numbers]"]
    lines += [f'"{path}" = {lum!r}' for path, lum in sorted(backgrounds.items())]
    lines += ["", "[numbers_format]", 'decimal = "."' + "  # разделитель дроби на столе"]

    (EYES_DIR / f"{PRESET}.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return calib_id


def _save_templates(cards: dict[str, Image.Image], calib_id: str) -> Path:
    out = EYES_DIR / "templates" / PRESET
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("*.png"):  # шаблоны чужих калибровок не смешиваются
        stale.unlink()
    for name, img in cards.items():
        img.save(out / name)
    (out / "calibration_id").write_text(calib_id + "\n", encoding="utf-8")
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://127.0.0.1:8011")
    p.add_argument("--viewport", default="1280x720")
    args = p.parse_args()
    vw, vh = (int(x) for x in args.viewport.split("x"))

    with TableTruthFeed(args.url, viewport=(vw, vh), bots=5) as feed:
        seats: list[str] = feed.js(SEATS_JS)
        layouts: list[list[str]] = feed.js(LAYOUTS_JS)
        truth = feed.wait_frozen()
        while truth is None or truth.get("hand_over"):  # герой должен быть в руке
            feed.act_and_confirm(truth)
            truth = feed.wait_frozen()
        feed.js(UNFOLD_JS)  # стол заморожен на ходе героя: инъекции никто не перезапишет

        slots, rects = _measure_cards(feed, seats)
        cards = _calibrate_cards(feed, seats, slots)
        number_zones = measure_number_zones(feed, seats)
        backgrounds = zone_backgrounds(feed, number_zones)
        rects.update(number_zones)

        calib_id = _write_preset((vw, vh), slots, rects, backgrounds, layouts, cards)
        out = _save_templates(cards, calib_id)
        feed.reload()  # вернуть странице настоящий вид игры
        CardReader(load_preset(PRESET))
        _selftest_numbers(feed, seats[0])

        classes = sorted({n.rsplit("_", 1)[1] for n in cards if n.startswith("card_")})
        print(f"пресет: {EYES_DIR / (PRESET + '.toml')} (калибровка {calib_id})")
        print(f"шаблоны: {len(cards)} -> {out} (карты: {len(LABELS)} в классах {', '.join(classes)}); "
              f"зон чисел: {len(number_zones)}")


if __name__ == "__main__":
    main()
