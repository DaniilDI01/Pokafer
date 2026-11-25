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
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

from PIL import Image, ImageChops

from pokafer.eyes.browser import TableBrowser
from pokafer.eyes.capture import ScreenCapture
from pokafer.eyes.sites import Preset

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

DOM_STATUS_JS = "return document.getElementById('status').textContent;"

DOM_ACT_JS = """
document.getElementById({next: 'nextHandFloat', call: 'checkCallBtn',
                        raise: 'raiseBtn'}[arguments[0]]).click();
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


def _key(truth: dict) -> tuple:
    """Состояние, меняющееся после ЛЮБОГО действия героя."""
    return (truth.get("hand_no"), bool(truth.get("hand_over")),
            truth.get("to_act"), tuple(truth.get("board") or []))


def is_frozen(truth: dict) -> bool:
    """Сервер не меняет состояние, пока герой не сходил."""
    return bool(truth) and (bool(truth.get("hand_over")) or truth.get("to_act") == HERO_SEAT)


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
    zones = {"hero.stack": line(one["hero.amt"], one["hero.plate"][0] + one["hero.plate"][2] + 8),
             "act.call_amount": line(one["act.call_amount"],
                                     one["act.call_amount"][0] + one["act.call_amount"][2] + 1)}
    zones["hero.bet"] = _chip(one["hero.bet"])
    zones["pot.amount"] = line(pot, one["pot.box"][0] + one["pot.box"][2])
    for pos in seats:
        amt, plate = one[f"seat.{pos}.amt"], one[f"seat.{pos}.plate"]
        zones[f"seat.{pos}.stack"] = line(amt, plate[0] + plate[2] + 8)
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

    @property
    def hand_no(self) -> int:
        return self.truth.get("hand_no", 0)

    @property
    def hand_over(self) -> bool:
        return bool(self.truth.get("hand_over"))


class TableTruthFeed:
    """Живой стол с правдой: замороженные кадры + DOM-инъекции калибровки."""

    def __init__(self, url: str, viewport: tuple[int, int] = (1280, 720),
                 bots: int = 5, headless: bool = True, raise_prob: float = 0.0,
                 screen: bool = False):
        self.base = url.rstrip("/")
        self.sid = uuid.uuid4().hex[:10]
        self.viewport = viewport
        self.raise_prob = raise_prob
        # screen=True — кадры с ЭКРАНА (боевой путь: mss поверх видимого окна),
        # а не CDP-скриншот; окно находится на экране при первом кадре
        self.screen = screen and not headless
        self._capture: ScreenCapture | None = None
        self.anomalies: Counter = Counter()
        self.browser = TableBrowser(f"{self.base}/?sid={self.sid}",
                                    viewport=viewport, headless=headless)
        self._wait_ready()
        for attempt in range(3):
            missing = bots - len(self.truth().get("bots") or [])
            for _ in range(max(missing, 0)):
                self.browser.execute(DOM_ADD_BOT_JS)
                time.sleep(0.05)
            if len(self.truth().get("bots") or []) >= bots:
                break
            time.sleep(0.3)
        if len(self.truth().get("bots") or []) != bots:
            self.close()
            raise RuntimeError(f"не удалось посадить {bots} ботов")

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
        """Сырой кадр: CDP-скриншот viewport'а или (screen=True) та же область экрана."""
        if not self.screen:
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
        """Ровно одно действие по правде: новая рука / колл-чек / изредка рейз."""
        if truth.get("hand_over"):
            self.browser.execute(DOM_ACT_JS, "next")
            return "next"
        legal = truth.get("legal") or {}
        can_raise = bool(legal.get("raise"))
        if can_raise and random.random() < self.raise_prob:
            self.browser.execute(DOM_ACT_JS, "raise")
            return "raise"
        self.browser.execute(DOM_ACT_JS, "call")  # кнопка «Чек/Колл»
        return "call"

    def act_and_confirm(self, truth: dict) -> bool:
        """Действие принято, когда сервер ушёл с этого замороженного состояния."""
        for _ in range(3):
            self.act(truth)
            end = time.time() + 3.0
            while time.time() < end:
                if _key(self.truth()) != _key(truth):
                    return True
                time.sleep(0.02)
            self.anomalies["act_lost"] += 1
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

    def _wait_ready(self, timeout: float = 10.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            try:
                status = self.browser.execute(DOM_STATUS_JS)
                if status != "подключение…" and self.truth().get("hand_no"):
                    return
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
