"""Правда нашего стола для калибровки и автотестов глаз — НЕ рантайм.

Рантайм глаз читает только пиксели кадра; этот модуль — мост к веб-столу на
время калибровки и тестов: открывает стол своим браузером (именованная сессия
-> GET /debug/state?sid=), играет за героя DOM-кликами и выдаёт ЗАМОРОЖЕННЫЕ
кадры с правдой.

Замороженное состояние — ход героя или оконченная рука: сервер его не меняет,
пока герой не сходил. Пара «кроп <-> правда» честна по построению: кадр
снимается только в замороженном состоянии, после того как отрисованные карты
совпали с правдой (страница догнала сервер) и краска устоялась (два подряд
одинаковых снимка — CDP может снять кадр до отрисовки).
"""
from __future__ import annotations

import json
import math
import random
import time
import urllib.request
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image, ImageChops

from pokafer.eyes.browser import TableBrowser
from pokafer.eyes.capture import ScreenCapture, WindowHidden
from pokafer.eyes.sites import Preset
from pokafer.eyes.table_setup import SEAT_BOTS_JS, STATUS_JS

if TYPE_CHECKING:  # читатели — только для подписей; сама правда от них не зависит
    from pokafer.eyes.cards import Reading
    from pokafer.eyes.flags import FlagReading
    from pokafer.eyes.numbers import NumberReading
    from pokafer.eyes.tracker import Tracker

HERO_SEAT = 1

# ---- JS страницы: калибровке и тестам DOM разрешён, рантайму глаз — нет ----

# карты, отрисованные клиентом СЕЙЧАС, по всем слотам стола (сверка
# рендера с правдой / с инъекцией калибровки; рубашки — 'back')
DOM_CARDS_JS = """
    const cardOf = (el) => el.classList.contains('back') ? 'back' : el.dataset.card;
    const labels = (sel) => [...document.querySelectorAll(sel)].map(cardOf);
    const out = {hero: labels('.seat.hero .cards .card'), board: labels('#board .card')};
    for (const s of document.querySelectorAll('.seat.villain')) {
        const pos = [...s.classList].find((c) => c.startsWith('pos-'));
        out[pos.replace('pos-', 'pos_')] = labels('.seat.' + pos + ' .cards .card');
    }
    return out;
"""

DOM_STATUS_JS = STATUS_JS

DOM_ACT_JS = """
document.getElementById({next: 'nextHandFloat', call: 'checkCallBtn',
                        raise: 'raiseBtn', fold: 'foldBtn'}[arguments[0]]).click();
"""

# что страница показывает «горящим» сейчас: ход, значок дилера, кнопка следующей руки
DOM_FLAGS_JS = """
const num = (el) => el.dataset.seat ? +el.dataset.seat : 1;
const active = [...document.querySelectorAll('.seat.active')].map(num);
const marked = [...document.querySelectorAll('.seat')].filter((el) => {
  const m = el.querySelector('.button-mark'); return m && m.classList.contains('on');
}).map(num);
return {to_act: active.length === 1 ? active[0] : (active.length ? active : null), button: marked,
        over: !document.getElementById('nextHandFloat').disabled};
"""

# инъекции «по месту»: те же элементы, те же позиции, что в игре
DOM_SET_HERO_JS = "renderCards(document.getElementById('hCards'), arguments[0], 0);"
DOM_SET_BOARD_JS = "renderBoard(arguments[0]);"
DOM_SET_SEAT_JS = """
const box = document.querySelector('.seat.' + arguments[0] + ' .cards');  // pos_tl -> pos-tl
box.innerHTML = '';
if (arguments[1] === 'backs') renderCards(box, null, 2);
else if (arguments[1]) renderCards(box, arguments[1], 0);
"""

DOM_ADD_BOT_JS = "document.getElementById('addBotBtn').click();"

# фишка без ставки скрыта (и пустая скрыта тоже): показать с текстом для замера
SHOW_CHIPS_JS = ("for (const el of document.querySelectorAll('.bet-chip')) "
                 "{ el.textContent = '888'; el.style.display = ''; }")

# прямоугольник текста элемента от символа start до конца: префиксы («Колл »,
# «Банк: ») — не число; скрытый элемент или текста нет -> null
TEXT_RECT_JS = """
const el = document.querySelector(arguments[0]);
if (!el || !el.firstChild || el.firstChild.nodeType !== 3) return null;
const t = el.firstChild, from = arguments[1];
if (from >= t.length) return null;
const r = document.createRange(); r.setStart(t, from); r.setEnd(t, t.length);
const b = r.getBoundingClientRect();
return b.width > 0.5 ? [b.x, b.y, b.width, b.height] : null;
"""

# прямоугольники (CSS-пиксели viewport) по списку «имя -> селектор»
DOM_RECTS_JS = """
const box = (el) => { const r = el.getBoundingClientRect();
  return [r.x, r.y, r.width, r.height]; };
const out = {};
for (const [name, sel] of Object.entries(arguments[0]))
  out[name] = [...document.querySelectorAll(sel)].map(box);
return out;
"""


def state_key(truth: dict) -> tuple:
    """Состояние, меняющееся после ЛЮБОГО действия героя.

    Кроме номера руки, борда и номера ходящего — банк и деньги на столе.
    Одних первых трёх мало: герой чекает, бот за ним ставит — и ход
    возвращается к герою на той же улице; действие принято, а прежний ключ
    этого не замечал и стенд отправлял действие вторично.
    """
    hero = truth.get("hero") or {}
    bots = truth.get("bots") or []
    return (truth.get("hand_no"), bool(truth.get("hand_over")),
            truth.get("to_act"), tuple(truth.get("board") or []), truth.get("pot"),
            hero.get("stack"), hero.get("bet"),
            tuple((b.get("stack"), b.get("bet"), bool(b.get("folded"))) for b in bots))


def is_frozen(truth: dict) -> bool:
    """Сервер не меняет состояние, пока герой не сходил."""
    return bool(truth) and (bool(truth.get("hand_over")) or truth.get("to_act") == HERO_SEAT)


def _shown_flags(truth: dict) -> dict:
    """Что страница обязана показывать «горящим» для этого состояния (сравнение с DOM_FLAGS_JS)."""
    over = bool(truth.get("hand_over"))
    return {"to_act": None if over else truth.get("to_act"),
            "button": [truth.get("button_seat")], "over": over}


def expected_cards(truth: dict, preset: Preset) -> dict[str, str | None]:
    """Слот пресета -> что там по правде: метка карты | 'back' | 'covered' | None.

    None — слот пуст (сукно); 'covered' — на слоте вскрытых карт лежат рубашки.
    Перечислены только слоты, у которых есть эталон пустого места и чьё
    состояние однозначно (например, слот рубашек под вскрытой картой не
    проверяется). Позиции ботов — по раскладке пресета (meta.seat_layouts).
    """
    out: dict[str, str | None] = {}
    hero = (truth.get("hero") or {}).get("cards") or []
    for i, card in enumerate(hero):
        out[f"hero.cards[{i}]"] = card
    board = truth.get("board") or []
    for i in range(len(preset.node("board.slots"))):
        out[f"board.slots[{i}]"] = board[i] if i < len(board) else None
    bots = truth.get("bots") or []
    layouts = preset.zones.get("meta", {}).get("seat_layouts") or []
    layout = layouts[len(bots) - 1] if 0 < len(bots) <= len(layouts) else []
    for pos, bot in zip(layout, bots):
        cards = bot.get("cards")
        if isinstance(cards, list) and cards:          # вскрытие
            out[f"seat.{pos}.cards[0]"] = cards[0]
            out[f"seat.{pos}.cards[1]"] = cards[1] if len(cards) > 1 else None
        elif bot.get("folded"):                         # сбросил — карты не показываются
            for kind in ("cards", "backs"):
                out[f"seat.{pos}.{kind}[0]"] = None
        else:                                           # в игре, карты скрыты
            out[f"seat.{pos}.backs[0]"] = "back"
            out[f"seat.{pos}.cards[0]"] = "covered"
    return out


NO_NUMBER = "none"   # элемент на месте, но числа нет (кнопка «Чек»)


def fmt_amount(x: float) -> str:
    """Сумма так, как её показывает стол (fmt в app.js): без округления до целого."""
    v = math.floor(x * 100 + 0.5) / 100
    return f"{v:.2f}".rstrip("0").rstrip(".")


def expected_numbers(truth: dict, paths, seat_layouts: list[list[str]]) -> dict[str, str | None]:
    """Зона числа -> что там по правде: строка суммы | NO_NUMBER | None (элемента нет).

    paths — зоны вида 'hero.stack', 'seat.pos_tl.bet', 'pot.amount',
    'act.call_amount'; раскладка мест — meta.seat_layouts пресета.
    """
    hero = truth.get("hero") or {}
    bots = truth.get("bots") or []
    over = bool(truth.get("hand_over"))
    layout = seat_layouts[len(bots) - 1] if 0 < len(bots) <= len(seat_layouts) else []
    out: dict[str, str | None] = {}
    for path in paths:
        owner, _, kind = path.rpartition(".")
        if kind == "amount":                      # pot.amount
            out[path] = fmt_amount(truth.get("pot") or 0.0)
        elif path == "act.call_amount":
            call = (truth.get("legal") or {}).get("call")
            # в завершённой руке надпись кнопки — прошлая, не проверяется
            out[path] = None if over else (fmt_amount(call) if call is not None else NO_NUMBER)
        elif kind == "stack":
            bot = _seat_bot(owner, layout, bots)
            stack = hero.get("stack") if owner == "hero" else (bot or {}).get("stack")
            out[path] = fmt_amount(stack) if stack is not None else None
        elif kind == "bet":
            bot = _seat_bot(owner, layout, bots)
            bet = hero.get("bet") if owner == "hero" else (bot or {}).get("bet")
            out[path] = fmt_amount(bet) if bet else None   # без ставки фишка не показана
        else:
            raise KeyError(f"неизвестная зона числа {path!r}")
    return out


def card_ok(want: str | None, reading: Reading) -> bool:
    """Верно ли прочитан слот: want — метка карты | 'back' | 'covered' | None."""
    if want == "back":
        return reading.state == "back"
    if want == "covered":
        return reading.state == "unknown"    # нельзя принять за пусто или карту
    if want is None:
        return reading.state == "empty"
    return reading.state == "face" and reading.label == want


def number_ok(want: str | None, reading: NumberReading) -> bool:
    """Верно ли прочитана зона: want — строка суммы | NO_NUMBER | None (элемента нет)."""
    if want == NO_NUMBER:
        return reading.state == "none"
    if want is None:
        return reading.state == "empty"
    return reading.state == "number" and reading.value == float(want)


def _seat_number(owner: str, layout: list[str], bots: list[dict]) -> int | None:
    """Номер места движка, которое сидит на месте owner ('hero' или 'seat.pos_tl')."""
    if owner == "hero":
        return HERO_SEAT
    return (_seat_bot(owner, layout, bots) or {}).get("seat")


def expected_flags(truth: dict, paths, seat_layouts: list[list[str]]) -> dict[str, bool]:
    """Индикатор -> горит ли он по правде: чей ход, значок дилера, рука окончена.

    paths — индикаторы пресета ('hero.turn', 'seat.pos_tl.button', 'hand.over');
    раскладка мест — meta.seat_layouts пресета.
    """
    over = bool(truth.get("hand_over"))
    to_act = None if over else truth.get("to_act")
    button = truth.get("button_seat")
    bots = truth.get("bots") or []
    layout = seat_layouts[len(bots) - 1] if 0 < len(bots) <= len(seat_layouts) else []
    out: dict[str, bool] = {}
    for path in paths:
        owner, _, kind = path.rpartition(".")
        if kind == "turn":
            out[path] = to_act is not None and _seat_number(owner, layout, bots) == to_act
        elif kind == "button":
            out[path] = button is not None and _seat_number(owner, layout, bots) == button
        elif kind == "over" and owner == "hand":
            out[path] = over
        else:
            raise KeyError(f"неизвестный индикатор {path!r}")
    return out


def flag_ok(want: bool, reading: FlagReading) -> bool:
    """Верно ли прочитан индикатор: want — горит ли он по правде."""
    return reading.state == ("on" if want else "off")


def records(actions) -> list[tuple]:
    """Действия в сравнимый вид: (порядок, улица, место, тип, «до», сколько добавлено).

    Принимает и записи трекера (ActionRecord), и движка (словари из /debug/hand).
    """
    def get(a, name):
        return a.get(name) if isinstance(a, dict) else getattr(a, name)
    return [(get(a, "order"), get(a, "street"), get(a, "seat"), get(a, "type"), get(a, "amount_to"),
             round(get(a, "amount"), 4)) for a in actions]


def _paid(records_: list[tuple]) -> dict:
    """Сколько каждое место внесло по этим действиям."""
    out: dict[int, float] = {}
    for record in records_:
        out[record[2]] = round(out.get(record[2], 0.0) + record[5], 4)
    return out


def _uncalled_raise(mine: list[tuple], truth: list[tuple]) -> bool:
    """Истории совпадают, кроме «рейзов, которые некому было назвать».

    Движок разрешает повышать, даже когда все остальные в олл-ине; непокрытая
    часть такого рейза возвращается, и по кадрам он неотличим от колла —
    трекер записывает колл. Рейз «пустой», если на этой улице никто больше не
    внёс сверх суммы колла.
    """
    if len(mine) != len(truth):
        return False
    for m, t in zip(mine, truth):
        if m == t:
            continue
        _, street, seat, kind, to, _ = m
        if kind != "call" or t[1:4] != (street, seat, "raise"):
            return False
        matched = max((round(sum(o[5] for o in truth if o[1] == street and o[2] == other), 4)
                       for other in {o[2] for o in truth} if other != seat), default=0.0)
        if matched > to + 1e-6:                  # рейз всё-таки называли
            return False
    return True


def history_verdict(mine, truth, gaps: int) -> str:
    """Как восстановленная трекером история соотносится с историей движка.

      точно         — действие в действие;
      пропуск       — трекер сам пометил, что интервал не восстановился (gaps > 0);
      эквивалент    — то же самое, кроме «рейза, который некому было назвать»
                      (см. _uncalled_raise);
      иной рассказ  — другое объяснение с теми же вложениями каждого места;
      выдумано      — вложения не совпадают: так бывает только при ошибке.

    mine и truth — списки из records().
    """
    if mine == truth:
        return "точно"
    if gaps:
        return "пропуск"
    if _uncalled_raise(mine, truth):
        return "эквивалент"
    return "иной рассказ" if _paid(mine) == _paid(truth) else "выдумано"


LEVEL = "уровень"          # всё видимое: обязано совпадать всегда
HISTORY = "история"        # действия: точно (см. history_verdict: эквивалент, иной рассказ, пропуск, выдумано)


def compare_hand(tracker: Tracker, hand: dict, view: dict) -> tuple[list[str], str]:
    """Срез трекера против правды: (что не сошлось в уровне, вердикт по истории).

    hand — раздача движка целиком (TableTruthFeed.hand), view — вид героя
    (TableTruthFeed.truth); оба — того же состояния, что и срез трекера.
    """
    snap = tracker.snapshot.model_dump()
    over = view["hand_over"]
    errors: list[str] = []

    def same(name: str, mine, want) -> None:
        if mine != want:
            errors.append(f"{name}: правда {want!r}, у трекера {mine!r}")

    same("борд", snap["board"], hand["board"])
    same("улица", snap["street"], hand["street"])
    same("дилер", snap["button_seat"], hand["button_seat"])
    same("банк", snap["pot"], round(float(view["pot"]), 2))
    same("ход героя", tracker.hero_turn, view["to_act"] == HERO_SEAT and not over)

    def seat(s: dict, keep: bool) -> tuple:
        return (s["seat"], round(s["stack"], 2), round(s["bet_street"], 2),
                round(s["invested"], 2) if keep else 0, s["folded"], s["all_in"])

    keep = tracker.from_start                  # история началась не с раздачи карт: вложения неполны
    same("места", [seat(s, keep) for s in snap["seats"]], [seat(s, keep) for s in hand["seats"]])
    if tracker.from_start:                     # рука, не виденная с раздачи: карты героя могут быть неизвестны
        same("карты героя", snap["seats"][0]["hole_cards"], hand["seats"][0]["hole_cards"])

    return errors, history_verdict(records(snap["actions"]), records(hand["actions"]), tracker.hand.gaps)


class Judge:
    """Счёт сверки с движком: каждое состояние, принятоное трекером, — ровно один раз.

    Приёмки трекера и боя (scripts/test_*_accuracy.py) зовут judge в тот
    кадр, когда трекер принял состояние (Tracker.serial вырос), и только
    если стол не ушёл с него: иначе сверялись бы разные моменты. Сводка и
    критерий — report и passed.
    """

    def __init__(self) -> None:
        self.stat: dict = defaultdict(lambda: [0, 0])   # разрез -> [верно, всего]
        self.fails: list[str] = []
        self.confirmed = 0                              # состояний сверено

    def judge_live(self, feed: "TableTruthFeed", tracker: Tracker, truth: dict) -> tuple[list[str], str] | None:
        """Сверить состояние, принятое трекером сейчас, с движком живого стола.

        Правда читается, пока стол может уйти вперёд (ходят боты): рука — у
        движка, затем правда сверяется с прочитанной до кадра. Ушёл — срез
        не судим (None): сверялись бы разные моменты. На ходе героя стол
        заморожен — там зовут judge напрямую.
        """
        hand = feed.hand()
        if state_key(feed.truth()) != state_key(truth):
            return None
        return self.judge(tracker, hand, truth)

    def judge(self, tracker: Tracker, hand: dict, view: dict) -> tuple[list[str], str]:
        if tracker.hand is None:                        # стол встречен между раздачами: сверять нечего
            return [], ""
        self.confirmed += 1
        errors, verdict = compare_hand(tracker, hand, view)
        for name, ok in (("срезы", not errors), (LEVEL, not errors), (HISTORY, verdict == "точно")):
            self.stat[name][1] += 1
            self.stat[name][0] += ok
        self.stat[(HISTORY, verdict)][1] += 1
        if len(self.fails) < 15:
            if verdict == "выдумано":
                self.fails.append(f"рука {view['hand_no']}: история выдумана — трекер "
                                  f"{records(tracker.snapshot.actions)}, движок {records(hand['actions'])}")
            if errors:
                self.fails.append(f"рука {view['hand_no']}: " + "; ".join(errors))
        return errors, verdict

    def report(self) -> list[str]:
        """Строки сводки: уровень, история по вердиктам, примеры расхождений."""
        lines = []
        for name in (LEVEL, HISTORY):
            ok, n = self.stat[name]
            lines.append(f"  {name:9} {ok:5}/{n:<5} {ok / n:.2%}" if n else f"  {name}: нет")
        for (_, verdict), (_, n) in sorted((k, v) for k, v in self.stat.items() if isinstance(k, tuple)):
            lines.append(f"    история: {verdict:14} {n:4}")
        return lines + (["примеры расхождений:"] + [f"   {line}" for line in self.fails] if self.fails else [])

    @property
    def passed(self) -> bool:
        """Уровень — ни одного расхождения; история — ни одного выдуманного действия."""
        total = self.stat["срезы"][1]
        return bool(total) and self.stat[LEVEL][0] == total and self.stat[(HISTORY, "выдумано")][1] == 0


Rect = tuple[int, int, int, int]


# показать все фишки (скрытая без ставки и пустая скрыты) и заполнить поле суммы
# кнопки «Колл»: у зон должна быть высота, когда их замеряют
FILL_AMOUNTS_JS = """
for (const el of document.querySelectorAll('.bet-chip')) {
  if (!el.textContent.trim()) el.textContent = '888';
  el.style.display = '';
}
document.querySelector('#checkCallBtn .label').textContent = 'Колл';
document.getElementById('callAmount').textContent = '14.25';
"""


def measure_number_zones(feed: "TableTruthFeed", seats: list[str]) -> dict[str, Rect]:
    """Зоны чисел стола по DOM: где лежат САМИ числа — без подписей рядом.

    Возвращает 'hero.stack', 'hero.bet', 'pot.amount', 'act.call_amount',
    'seat.<место>.stack', 'seat.<место>.bet' -> (x, y, w, h). Числа на столе
    начинаются в фиксированных точках (поля сумм) и растут вправо, поэтому
    зона — от начала числа до края элемента. Вызывающий обязан после замера
    перерисовать страницу (feed.reload() или действие героя): фишки и кнопка
    на время замера показываются с заглушками.
    """
    feed.js(FILL_AMOUNTS_JS)
    selectors = {"hero.amt": "#hAmount", "hero.plate": ".seat.hero .plate", "hero.bet": "#hBet",
                 "pot.amt": "#potAmount", "pot.box": "#pot", "act.call_amount": "#callAmount"}
    for pos in seats:
        cls = pos.replace("_", "-")
        selectors.update({f"seat.{pos}.amt": f".seat.{cls} .stack .amt",
                          f"seat.{pos}.plate": f".seat.{cls} .plate",
                          f"seat.{pos}.bet": f".seat.{cls} .bet-chip"})
    found = feed.rects(selectors)
    if any(len(items) != 1 for items in found.values()):
        bad = {k: len(v) for k, v in found.items() if len(v) != 1}
        raise RuntimeError(f"не удалось замерить зоны чисел (элементов не по одному): {bad}")
    one = {name: items[0] for name, items in found.items()}

    def line(text: list[float], right: float) -> Rect:
        """Строка числа: от левого края текста (−1 px) до right, строки текста ±1 px."""
        top = math.floor(text[1] + 1e-6) - 1
        return (round(text[0]) - 1, top, round(right) - round(text[0]) + 2,
                math.ceil(text[1] + text[3] - 1e-6) - top + 1)

    pot = one["pot.amt"]                      # поле числа: подпись «Банк: » — не оно
    # зона стека кончается краем таблички: за ним сукно, а на вскрытии — карта
    # места. Запас в 8 px за краем попадал в зону чтения, и одиночный «0» стека
    # олл-ина в широкой зоне модель читала как «。」 (найдено в бою)
    zones = {"hero.stack": line(one["hero.amt"], one["hero.plate"][0] + one["hero.plate"][2]),
             "act.call_amount": line(one["act.call_amount"],
                                     one["act.call_amount"][0] + one["act.call_amount"][2] + 1)}
    zones["hero.bet"] = _chip(one["hero.bet"])
    zones["pot.amount"] = line(pot, one["pot.box"][0] + one["pot.box"][2])
    for pos in seats:
        amt, plate = one[f"seat.{pos}.amt"], one[f"seat.{pos}.plate"]
        zones[f"seat.{pos}.stack"] = line(amt, plate[0] + plate[2])
        zones[f"seat.{pos}.bet"] = _chip(one[f"seat.{pos}.bet"])
    for path, (x, y, w, h) in zones.items():
        if w < 8 or h < 8:
            raise RuntimeError(f"зона числа {path!r} получилась {(x, y, w, h)} — элемент скрыт "
                               "или отсутствует на столе")
    return zones


def _chip(rect: list[float]) -> Rect:
    """Зона числа фишки: внутри скруглённых углов пилюли (углы — не число)."""
    x, y, w, h = (round(v) for v in rect)
    return (x + 4, y + 3, w - 8, h - 6)


def zone_backgrounds(feed: "TableTruthFeed", zones: dict[str, Rect]) -> dict[str, float]:
    """Яркость фона каждой зоны (медиана по кадру с числами): по ней читатель
    отличает «элемент скрыт» от «число есть»."""
    frame = feed.settled()
    if frame is None:
        raise RuntimeError("краска не устоялась на замере фона зон чисел")
    out = {}
    for path, (x, y, w, h) in zones.items():
        lums = sorted(frame.crop((x, y, x + w, y + h)).convert("L").getdata())
        out[path] = float(lums[len(lums) // 2])
    return out


def _seat_bot(owner: str, layout: list[str], bots: list[dict]) -> dict | None:
    """Бот, сидящий на месте owner ('seat.pos_tl'); None — места нет в раскладке."""
    if not owner.startswith("seat."):
        return None
    pos = owner.split(".", 1)[1]
    return dict(zip(layout, bots)).get(pos)


@dataclass(frozen=True)
class TruthFrame:
    truth: dict
    image: Image.Image
    hand: dict | None = None     # раздача с историей действий движка (только у samples)

    @property
    def hand_no(self) -> int:
        return self.truth.get("hand_no", 0)

    @property
    def hand_over(self) -> bool:
        return bool(self.truth.get("hand_over"))


class TableTruthFeed:
    """Живой стол с правдой: замороженные кадры + DOM-инъекции калибровки."""

    def __init__(self, url: str, viewport: tuple[int, int] = (1280, 720),
                 bots: int = 5, strategy: str = "heuristic", headless: bool = True, raise_prob: float = 0.0,
                 screen: bool = False, fold_prob: float = 0.0):
        self.base = url.rstrip("/")
        self.sid = uuid.uuid4().hex[:10]
        self.viewport = viewport
        self.raise_prob = raise_prob
        self.fold_prob = fold_prob
        # screen=True — кадры с ЭКРАНА (запасной путь: mss поверх видимого
        # окна), а не пиксели страницы от браузера; окно находится на экране
        # при первом кадре и должно оставаться видно целиком
        self.screen = screen and not headless
        self._capture: ScreenCapture | None = None
        self.anomalies: Counter = Counter()
        self.browser = TableBrowser(f"{self.base}/?sid={self.sid}",
                                    viewport=viewport, headless=headless)
        self._wait_ready()
        self._seat_bots(bots, strategy)

    # ---------- правда ----------

    def truth(self) -> dict:
        with urllib.request.urlopen(f"{self.base}/debug/state?sid={self.sid}",
                                    timeout=3) as r:
            return json.loads(r.read())

    def wait_frozen(self, timeout: float = 15.0) -> dict | None:
        """Правда замороженного состояния (ход героя / рука окончена)."""
        end = time.time() + timeout
        while time.time() < end:
            truth = self.truth()
            if is_frozen(truth):
                return truth
            time.sleep(0.03)
        self.anomalies["not_frozen"] += 1
        return None

    def wait_dom_sync(self, truth: dict, timeout: float = 3.0) -> bool:
        """Страница догнала сервер: отрисованные карты совпали с правдой."""
        hero = (truth.get("hero") or {}).get("cards") or []
        board = truth.get("board") or []
        end = time.time() + timeout
        while time.time() < end:
            dom = self.browser.execute(DOM_CARDS_JS)
            if dom["hero"] == hero and dom["board"] == board:
                return True
            time.sleep(0.03)
        self.anomalies["dom_lag"] += 1
        return False

    def dom_cards(self) -> dict:
        return self.browser.execute(DOM_CARDS_JS)

    def frame(self) -> Image.Image:
        """Сырой кадр: пиксели viewport'а из браузера или (screen=True) та же область экрана.

        Свернуть окно во время теста (обычно — руками) не должно ронять стенд:
        окно разворачивается, кадр снимается; случай попадает в аномалии.
        """
        if not self.screen:
            try:
                return self.browser.screenshot()
            except WindowHidden:
                self.anomalies["hidden"] += 1
                print("правда: окно браузера было скрыто (свёрнуто) — разворачиваю")
                self.browser.set_window_state("normal")
                time.sleep(0.5)
                return self.browser.screenshot()
        if self._capture is None:
            x, y = self.browser.locate_on_screen()
            vw, vh = self.viewport
            self._capture = ScreenCapture((x, y, vw, vh))
        return self._capture.grab()

    def settled(self, tries: int | None = None, settle_ms: int = 60) -> Image.Image | None:
        """Кадр с устоявшейся краской: два подряд одинаковых снимка.

        С экрана допускается горстка отличившихся пикселей (мигающий курсор
        ввода и т.п.) и больше попыток — экран отстаёт от страницы на кадр-два
        и плавно показывает переходы CSS (фолд места затухает 0.25 с);
        CDP-кадры сравниваются строго.
        """
        tolerance = 32 if self.screen else 0
        tries = tries or (8 if self.screen else 4)
        prev = self.frame()
        for _ in range(tries):
            time.sleep(settle_ms / 1000)
            cur = self.frame()
            changed = ImageChops.difference(prev, cur).convert("L").histogram()
            if sum(changed[1:]) <= tolerance:
                return cur
            prev = cur
        self.anomalies["paint"] += 1
        return None

    # ---------- игра за героя (DOM-клики: только калибровка/тест) ----------

    def act(self, truth: dict) -> str:
        """Ровно одно действие по правде: новая рука / колл-чек / изредка рейз или фолд."""
        if truth.get("hand_over"):
            self.browser.execute(DOM_ACT_JS, "next")
            return "next"
        legal = truth.get("legal") or {}
        if legal.get("fold") and random.random() < self.fold_prob:
            self.browser.execute(DOM_ACT_JS, "fold")
            return "fold"
        if legal.get("raise") and random.random() < self.raise_prob:
            self.browser.execute(DOM_ACT_JS, "raise")
            return "raise"
        self.browser.execute(DOM_ACT_JS, "call")  # кнопка «Чек/Колл»
        return "call"

    def act_and_confirm(self, truth: dict) -> bool:
        for _ in range(3):
            self.act(truth)
            if self.accepted(truth):
                return True
            status = self.browser.execute(DOM_STATUS_JS)
            if status.startswith("JS-ошибка"):        # страница сломалась в игре: не молчать
                raise RuntimeError(f"страница стола упала с ошибкой: {status}")
            self.anomalies["act_lost"] += 1
        return False

    def accepted(self, truth: dict, timeout: float = 3.0) -> bool:
        """Сервер ушёл с этого замороженного состояния — действие принято.

        Так подтверждают и DOM-клик стенда, и РЕАЛЬНЫЙ клик актатора
        (scripts/test_window_states.py).
        """
        end = time.time() + timeout
        while time.time() < end:
            if state_key(self.truth()) != state_key(truth):
                return True
            time.sleep(0.02)
        return False

    def frames(self, seconds: float) -> Iterator[TruthFrame]:
        """По одному замороженному кадру на каждое решение героя / итог руки."""
        end = time.time() + seconds
        while time.time() < end:
            truth = self.wait_frozen()
            if truth is None:
                print("правда: стол не замораживается (ход не у героя) — останавливаюсь")
                return
            self.wait_dom_sync(truth)
            image = self.settled()
            if image is not None:
                yield TruthFrame(truth, image)
            if time.time() >= end:
                return
            if not self.act_and_confirm(truth):
                print(f"правда: стол не принимает действия героя (статус "
                      f"{self.browser.execute(DOM_STATUS_JS)!r}) — останавливаюсь")
                return

    def hand(self) -> dict:
        """Раздача с историей действий движка (GET /debug/hand): срез мозга с точки зрения героя."""
        with urllib.request.urlopen(f"{self.base}/debug/hand?sid={self.sid}", timeout=3) as r:
            return json.loads(r.read())

    def synced_truth(self, timeout: float = 3.0) -> dict | None:
        """Правда, которую страница УЖЕ показывает (ход, дилер, кнопка следующей руки).

        Читается заново на каждой попытке: пока стол играет, состояние уходит
        вперёд, и сверка с устаревшей правдой ждала бы до тайм-аута.
        """
        end = time.time() + timeout
        while time.time() < end:
            truth = self.truth()
            if self.browser.execute(DOM_FLAGS_JS) == _shown_flags(truth):
                return truth
            time.sleep(0.02)
        self.anomalies["dom_flags_lag"] += 1
        return None

    def samples(self, seconds: float) -> Iterator[TruthFrame]:
        """Кадры ЖИВОЙ игры — и ходы ботов, и итог руки — с правдой того же мгновения.

        Замороженные кадры (.frames) не видят ходов ботов, а индикаторам и
        трекеру нужны все состояния. Пара «кадр <-> правда» честна, пока
        состояние не менялось: страница догнала сервер (synced_truth), краска
        устоялась, правда ДО и ПОСЛЕ съёмки совпала (state_key). Снятое замороженное
        состояние герой закрывает одним действием, и игра идёт дальше; не
        снятое — снимается в следующем круге, а не пропускается.
        """
        end = time.time() + seconds
        while time.time() < end:
            truth = self.synced_truth()
            if truth is None:
                continue
            image = self.settled()
            if image is None:
                continue
            hand = self.hand()                       # пока состояние то же — правда та же
            if state_key(self.truth()) != state_key(truth):
                continue
            yield TruthFrame(truth, image, hand)
            if is_frozen(truth) and not self.act_and_confirm(truth):
                print(f"правда: стол не принимает действия героя (статус "
                      f"{self.browser.execute(DOM_STATUS_JS)!r}) — останавливаюсь")
                return

    # ---------- инъекции «по месту» (калибровка шаблонов) ----------

    def set_hero_cards(self, cards: list[str]) -> None:
        self.browser.execute(DOM_SET_HERO_JS, cards)

    def set_board(self, cards: list[str]) -> None:
        self.browser.execute(DOM_SET_BOARD_JS, cards)

    def set_seat_cards(self, pos: str, cards: list[str] | str | None) -> None:
        """cards: список карт | 'backs' | None (пустой бокс)."""
        self.browser.execute(DOM_SET_SEAT_JS, pos.replace("_", "-"), cards)

    def rects(self, selectors: dict[str, str]) -> dict[str, list[list[float]]]:
        """CSS-пиксели элементов по селекторам: имя -> [прямоугольники]."""
        return self.browser.execute(DOM_RECTS_JS, selectors)

    def text_rect(self, selector: str, start: int = 0) -> list[float] | None:
        """CSS-пиксели текста элемента от символа start до конца (число без префикса)."""
        return self.browser.execute(TEXT_RECT_JS, selector, start)

    def js(self, script: str, *args):
        """Произвольный JS страницы (калибровочные скрипты; не рантайм)."""
        return self.browser.execute(script, *args)

    def reload(self) -> None:
        self.browser.execute("location.reload();")
        self._wait_ready()

    # ---------- служебное ----------

    def _seat_bots(self, bots: int, strategy: str = "heuristic", timeout: float = 15.0) -> None:
        """Посадить ботов и дождаться, пока сервер их примет.

        Сервер читает следующую команду клиента, только доиграв ходы ботов
        (пауза bot_think на каждый): при 0.5 с пересборка стола запаздывает
        на секунды. Стандартные боты (heuristic) сажаются кнопкой «+ бот»:
        клиент не шлёт больше пяти — лишние клики не мешают. Иная стратегия —
        стол пересобирается одной командой (table_setup.SEAT_BOTS_JS).
        """
        end = time.time() + timeout
        if strategy != "heuristic":
            self.browser.execute(SEAT_BOTS_JS, bots, strategy)
        while time.time() < end:
            seated = self.truth().get("bots") or []
            if len(seated) >= bots and all(b.get("strategy") == strategy for b in seated):
                return
            if strategy == "heuristic" and self.browser.execute("return botsCfg.length;") < bots:
                self.browser.execute(DOM_ADD_BOT_JS)
            time.sleep(0.1)
        self.close()
        raise RuntimeError(f"не удалось посадить {bots} ботов ({strategy})")

    def _wait_ready(self, timeout: float = 10.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            try:
                status = self.browser.execute(DOM_STATUS_JS)
                if status.startswith("JS-ошибка"):   # страница жива, но сломана: клики тихо терялись бы
                    self.close()
                    raise RuntimeError(f"страница стола упала с ошибкой: {status}")
                if status != "подключение…" and self.truth().get("hand_no"):
                    return
            except RuntimeError:
                raise
            except Exception:  # страница ещё грузится / сессии ещё нет
                pass
            time.sleep(0.1)
        raise RuntimeError("стол не открылся: нет сессии или соединения")

    def close(self) -> None:
        if self._capture is not None:
            self._capture.close()
        self.browser.close()

    def __enter__(self) -> "TableTruthFeed":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
