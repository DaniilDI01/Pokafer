"""Трекер: поток Perception -> подтверждённое состояние раздачи -> Snapshot.

Конвейер-агрегатор между глазами и мозгом. Глаза видят один кадр и ничего не
помнят; трекер копит увиденное во времени и выдаёт то, что понимает мозг:
Snapshot (pokafer/core/schema.py) и события.

Как здесь всё устроено:

- **Уровень** (level.py): сырые чтения кадра -> состояние стола; кадр, которому
  верить нельзя, не подтверждает ничего.
- **Подтверждение**: состояние становится фактом, только если видно в двух
  подряд одинаковых кадрах. Один сбойный кадр ничего не меняет.
- **Уровень — из кадра**: стеки, ставки, банк, борд, сбросившие — прямые
  показания. **История — накоплением**: действия между двумя подтверждёнными
  состояниями восстанавливает поиск по правилам покера (history.py). Точного
  объяснения нет — трекер отмечает пропуск и ничего не выдумывает.
- **Свидетели**: состояние, увиденное один раз, уровня не меняет, но сужает
  объяснения — так «кто поставил» виден даже при 2–3 кадрах в секунду.
- **Рука**: начинается, когда у героя прочитаны карты (и рука не окончена);
  блайнды записываются по кнопке; окончена — по индикатору «рука окончена»
  (без него — когда остался один не сбросивший). Рука, прошедшая между
  подтверждениями целиком, начинается с своего итога и честно помечается
  неполной; сойденные деньги (банк не равен сумме ушедшего со стеков) —
  рассинхронизация.

Места в Snapshot нумеруются по часовой стрелке от героя (герой — 1), только
занятые, — как их нумерует движок нашего стола. Snapshot всегда с точки зрения
героя (actor_seat = 1, is_hero у героя): мозг решает только на ходу героя,
а наблюдать (Brain.observe) может любой срез.

Граница: трекер — чистая логика. На входе Perception, на выходе Snapshot и
события; пикселей, браузера и DOM здесь нет (закреплено тестом).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from pokafer.core.schema import ActionRecord, SeatState, Snapshot
from pokafer.eyes.history import EPS, STREETS, Goal, Round, blinds, clockwise, next_seat, plan, shape, street_of
from pokafer.eyes.level import Level, Table, key, level_of
from pokafer.eyes.sites import Preset

if TYPE_CHECKING:
    from pokafer.eyes.perceive import Perception

HERO = 1                                # номер героя в Snapshot
POT_TOL = 0.005                         # больше — банк не сходится с деньгами на столе


@dataclass(frozen=True)
class Event:
    """Что трекер установил на кадре (feed возвращает список)."""

    kind: str                           # hand_start | actions | street | hero_turn | hand_over | gap | resync
    actions: tuple[ActionRecord, ...] = ()


@dataclass
class _Seat:
    seat: int
    pos: str
    stack: float
    bet: float = 0.0
    folded: bool = False
    all_in: bool = False                # стек 0 в раздаче (как у движка, остаётся и в итоге руки)
    cards: list[str] | None = None      # карты героя; чужие — только вскрытые


@dataclass
class _Hand:
    no: int
    hero_cards: tuple[str, ...]
    button: int | None
    seats: dict[int, _Seat]
    board: list[str] = field(default_factory=list)
    pot: float = 0.0
    actions: list[ActionRecord] = field(default_factory=list)
    start: dict[int, float] | None = None   # стеки на старте: из них вложения считаются точно
    exact: bool = True                      # история текущей улицы восстановлена точно
    gaps: int = 0                           # пропуски за руку
    from_start: bool = True                 # рука видна с раздачи карт (иначе карты героя могут быть неизвестны)


def _folded(pos: str, seat, hero_cards: tuple[str, ...]) -> bool:
    """Сбросил ли: у чужих карт нет; карты героя приглушаются и перестают читаться."""
    return not hero_cards if pos == "hero" else not seat.in_hand


class Tracker:
    """Кормить кадрами (feed), забирать события, Snapshot и подтверждённый ход героя."""

    def __init__(self, preset: Preset, table_id: str | None = None):
        self.table = Table.from_preset(preset)
        self.table_id = table_id or preset.name
        self.phase = "idle"              # idle | in_hand | over
        self.hand: _Hand | None = None
        self.level: Level | None = None  # последний подтверждённый уровень
        self.hero_turn = False           # ход героя подтверждён (кнопки горят, место светится)
        self.serial = 0                  # номер принятого состояния: растёт только на новом (одно решение на состояние)
        self.changing = False            # виден кадр, отличный от принятого состояния, но ещё не подтверждённый
        self.stats: Counter = Counter()
        self._hands = 0
        self._pending: tuple[tuple, Level] | None = None   # состояние, пока увиденное один раз
        self._singles: list[Level] = []                    # увиденные однажды между подтверждениями
        self._last_stacks: dict[str, float] | None = None  # стеки на конец прошлой руки, по местам
        self._last_button: str | None = None               # место дилера в прошлой руке
        self._snapshot: Snapshot | None = None

    # ---------- вход и выход ----------

    def feed(self, perception: "Perception") -> list[Event]:
        """Один кадр. События — только на подтверждённых изменениях.

        То же состояние, увиденное снова, — не изменение: serial не растёт,
        история не ищется заново. Так serial — номер именно нового состояния
        (одно решение на состояние), а changing — «стол сейчас меняется, но
        ещё не подтверждено»: оба — для пилота (pokafer/eyes/pilot.py).
        """
        level = level_of(perception, self.table)
        if level.bad is not None:
            self.stats[f"кадр недостоверный: {level.bad}"] += 1
            return []
        seen = key(level, with_hero_cards=self.phase != "in_hand")
        adopted = None if self.level is None else key(self.level, self.phase != "in_hand")
        self.changing = seen != adopted                   # кадр отличается от принятого состояния
        if self._pending is not None and self._pending[0] == seen:
            self._pending = None                       # второй такой же кадр — состояние подтверждено
            self.stats["состояния подтверждены"] += 1
            self.changing = False                      # подтверждённое состояние — теперь оно и есть состояние
            return self._apply(level) if seen != adopted else []
        if self._pending is not None and self._pending[0] != adopted:
            self._singles.append(self._pending[1])     # ушло, не подтвердившись: слабое свидетельство
        self._pending = (seen, level)                  # первый раз: ждём второго
        return []

    @property
    def snapshot(self) -> Snapshot | None:
        """Подтверждённый срез раздачи в формате мозга; None — руки нет."""
        if self.hand is not None and self._snapshot is None:
            self._snapshot = self._build()
        return self._snapshot

    @property
    def from_start(self) -> bool:
        """Видна ли рука с раздачи карт (стол встречен на раздаче или рука прошла между кадрами — нет)."""
        return self.hand is not None and self.hand.from_start

    @property
    def exact(self) -> bool:
        """Восстановлена ли точно история текущей улицы (от неё зависит минимальный рейз)."""
        return self.hand is not None and self.hand.exact

    def view(self) -> dict:
        """Что трекер думает о раздаче — кратко и в JSON (зеркало, журнал, терминал)."""
        hand, level = self.hand, self.level
        if hand is None or level is None:
            return {"phase": self.phase, "hand": self._hands}
        seats = {s.pos: n for n, s in hand.seats.items()}
        return {
            "phase": self.phase, "hand": hand.no, "street": self.snapshot.street, "board": list(hand.board),
            "pot": hand.pot, "button": seats.get(level.button), "actor": seats.get(level.actor),
            "hero_turn": self.hero_turn, "call": level.call, "exact": hand.exact, "gaps": hand.gaps,
            "actions": len(hand.actions),
            "seats": [{"seat": n, "name": s.pos, "stack": s.stack, "bet": s.bet, "folded": s.folded,
                       "all_in": s.all_in} for n, s in sorted(hand.seats.items())],
        }

    # ---------- жизнь руки ----------

    def _apply(self, level: Level) -> list[Event]:
        self.serial += 1
        over = self._over(level)
        if self.phase == "in_hand" and not over and self._another_hand(level):
            self.stats["рука окончена незамеченная"] += 1
            return self._start(level)                  # конец прошлой руки проскочили без кадра
        if self.phase == "in_hand":
            return self._advance(level)
        if not over and level.hero_cards:
            return self._start(level)                  # новая рука (или стол встречен на раздаче)
        if self._another_hand(level):
            self.stats["рука прошла между кадрами"] += 1
            return self._start(level)                  # началась и окончилась, не подтвердившись ни разу
        if over:
            if self.phase == "idle":
                self.phase = "over"                    # стол встречен на итоге руки: не с начала
            else:
                self._refresh_over(level)              # вскрытые карты могут дорисоваться позже
        self.level = level
        return []

    def _another_hand(self, level: Level) -> bool:
        """Уровень — уже не та рука, что отслеживается: борд не начинается со старого (усох или
        карты другие) либо у героя прочитаны другие карты."""
        hand = self.hand
        if hand is None:
            return False
        return (level.board[:len(hand.board)] != tuple(hand.board)
                or bool(level.hero_cards) and level.hero_cards != hand.hero_cards)

    def _over(self, level: Level) -> bool:
        """Рука окончена: по индикатору; без него — когда в руке остался один."""
        if level.over is not None:
            return level.over
        return sum(1 for pos, s in level.seats.items()
                   if not _folded(pos, s, level.hero_cards)) <= 1 and bool(level.seats)

    def _start(self, level: Level) -> list[Event]:
        """Новая рука: разметить места, внести блайнды, восстановить всё, что уже успело произойти."""
        numbers = {pos: HERO + i for i, pos in enumerate(p for p in self.table.order if p in level.seats)}
        ring = sorted(numbers.values())
        seats = {}
        for pos, seen in level.seats.items():
            n = numbers[pos]
            seats[n] = _Seat(n, pos, seen.stack, seen.bet, _folded(pos, seen, level.hero_cards),
                             cards=list(level.hero_cards) if pos == "hero" else list(seen.cards) or None)
            seats[n].all_in = not seats[n].folded and seen.stack <= EPS
        self._hands += 1
        self.stats["руки"] += 1
        hand = self.hand = _Hand(self._hands, level.hero_cards, self._button_of(level, numbers, ring), seats)
        same_hand = [seen for seen in self._singles
                     if not self._over(seen) and seen.hero_cards == level.hero_cards]
        self._singles = same_hand                      # кадры прошлой руки — не свидетельства этой
        self.phase, self.level, self.hero_turn, self._snapshot = "in_hand", None, False, None
        events = [Event("hand_start")]
        start = None if self._over(level) else self._opening(level, ring)
        if start is None:                              # не из чего: история руки неполна с начала
            hand.exact = hand.from_start = False
            hand.gaps += 1
            self._adopt(level, self._over(level), events)
            return events
        hand.actions = list(start.actions)              # блайнды — первые действия руки
        self._settle(level, start, {n: start.stacks[n] - s.stack for n, s in hand.seats.items()},
                     self._over(level), events)
        return events

    def _advance(self, level: Level) -> list[Event]:
        hand = self.hand
        events = self._resync_seats(level)
        over = self._over(level)
        put = None
        if not over:                                   # кто сколько внёс за интервал — видно по стекам
            put = {n: s.stack - level.seats[s.pos].stack for n, s in hand.seats.items()}
        else:
            live = [n for n, s in hand.seats.items()
                    if not _folded(s.pos, level.seats[s.pos], level.hero_cards)]
            if len(live) == 1:                         # все сбросили: выигрыш одного известен
                put = {n: s.stack - level.seats[s.pos].stack + (level.pot if n == live[0] else 0.0)
                       for n, s in hand.seats.items()}
        self._settle(level, self._round(street_of(len(self.level.board))), put, over, events)
        return events

    def _settle(self, level: Level, start: Round, put: dict[int, float] | None, over: bool,
                events: list[Event]) -> None:
        """Восстановить действия от start до уровня и принять уровень как есть."""
        hand = self.hand
        via = tuple(self._shape(seen) for seen in self._singles if not self._over(seen))
        self._singles = []                             # итог руки свидетелем не бывает: ставки уже скрыты
        goal = self._goal(level, put, over, start.pot, via)
        records = plan(start, goal)
        if records is None and via:                    # свидетельству противоречит любая история:
            records = plan(start, replace(goal, via=()))  # оно было сбоем — история ищется без него
            if records is not None:
                self.stats["кадр-свидетель отброшен"] += 1
        new_street = street_of(len(level.board)) > start.street
        if records is None:
            hand.gaps += hand.exact
            hand.exact = False                         # до следующей улицы, восстановленной точно
            self.stats["интервал не восстановлен"] += 1
            events.append(Event("gap"))
        else:
            hand.actions.extend(records)
            if records:
                events.append(Event("actions", tuple(records)))
        if new_street:
            hand.exact = records is not None          # новая улица: её история считается заново
            events.append(Event("street"))
        self._adopt(level, over, events)

    def _adopt(self, level: Level, over: bool, events: list[Event]) -> None:
        """Уровень подтверждён: переносится в раздачу, проверяется на сходимость денег."""
        hand = self.hand
        for n, seat in hand.seats.items():
            seen = level.seats[seat.pos]
            seat.stack, seat.bet = seen.stack, 0.0 if over else seen.bet
            seat.folded = _folded(seat.pos, seen, level.hero_cards)
            # олл-ин мог прийти в одном кадре с итогом руки: тогда его видно по истории
            went_all_in = hand.start is not None and hand.start[n] - self._paid(n) <= EPS
            seat.all_in = seat.all_in or not seat.folded and (seen.stack <= EPS or over and went_all_in)
            if seat.pos != "hero":
                seat.cards = list(seen.cards) or None
        hand.board, hand.pot = list(level.board), level.pot
        self.level, self._snapshot = level, None
        if not over:
            self._check(level, events)
        turn = not over and not hand.seats[HERO].folded and self._hero_may_act(level)
        if turn and not self.hero_turn:
            events.append(Event("hero_turn"))
        self.hero_turn = turn
        if over:
            self.phase = "over"
            self._last_stacks = {seat.pos: seat.stack for seat in hand.seats.values()}
            self._last_button = hand.seats[hand.button].pos if hand.button in hand.seats else None
            events.append(Event("hand_over"))

    def _check(self, level: Level, events: list[Event]) -> None:
        """Банк равен деньгам, ушедшим со стеков (или сумме восстановленных действий)."""
        hand = self.hand
        if hand.start is not None:
            expected = sum(hand.start[n] - s.stack for n, s in hand.seats.items())
        elif hand.exact:
            expected = sum(a.amount for a in hand.actions)
        else:
            return                                      # сверять не с чем
        if abs(level.pot - expected) > POT_TOL:
            self.stats["банк не сходится с деньгами на столе"] += 1
            events.append(Event("resync"))
            hand.start, hand.exact = None, False

    def _hero_may_act(self, level: Level) -> bool:
        """Ход героя: кнопки действий горят; индикатор хода, если он есть, не противоречит."""
        by_flag = level.actor == "hero" if "turn" in self.table.flags else None
        return bool(by_flag) if level.hero_buttons is None else level.hero_buttons and by_flag is not False

    # ---------- начало руки и круги ----------

    def _button_of(self, level: Level, numbers: dict[str, int], ring: list[int]) -> int | None:
        """Место дилера: по индикатору; без него — по блайндам на префлопе, дальше — по кругу."""
        if level.button in numbers:
            return numbers[level.button]
        bets = {numbers[pos]: s.bet for pos, s in level.seats.items()}
        posted = [n for n in ring if bets[n] > EPS]
        if not level.board and len(posted) == 2:
            small = min(posted, key=lambda n: bets[n])
            return small if len(ring) == 2 else clockwise(ring, small)[-1]   # перед малым; в HU — он сам
        self.stats["кнопка не видна"] += 1
        if self._last_button is not None:               # кнопка ушла на следующее место по кругу
            order = [p for p in self.table.order if p in numbers]
            return numbers[order[(order.index(self._last_button) + 1) % len(order)]] \
                if self._last_button in order else numbers[order[0]]
        return None

    def _opening(self, level: Level, ring: list[int]) -> Round | None:
        """Начало руки по правилу: блайнды внесены, ходит первый после большого.

        None — начало не из чего восстановить (нет ни кнопки, ни стеков на
        старте): история руки неполна с первого действия.
        """
        hand = self.hand
        hand.start = self._start_stacks(level)
        if hand.button is None or hand.start is None and level.board:
            return None
        small, big = blinds(ring, hand.button)
        posted = {small: self.table.sb, big: self.table.bb}
        if hand.start is not None:
            posted = {n: min(amount, hand.start[n]) for n, amount in posted.items()}
        bets = {n: posted.get(n, 0.0) for n in ring}
        actions = [ActionRecord(order=i, street="preflop", seat=n, type="post",
                                amount_to=round(amount, 4), amount=round(amount, 4))
                   for i, (n, amount) in enumerate(posted.items())]
        first = next((n for n in clockwise(ring, big) if hand.seats[n].stack + hand.seats[n].bet - bets[n] > EPS),
                     None)
        return Round(tuple(ring), hand.button, self.table.sb, self.table.bb, 0, sum(bets.values()), bets,
                     {n: s.stack + s.bet - bets[n] for n, s in hand.seats.items()},
                     frozenset(), dict(bets), big, False, first, actions)

    def _start_stacks(self, level: Level) -> dict[int, float] | None:
        """Стеки на старте руки: с прошлого итога; на префлопе — стек кадра плюс его ставка."""
        last, hand = self._last_stacks, self.hand
        preflop = not level.board
        if last is not None and all(s.pos in last for s in hand.seats.values()) and (
                not preflop or all(abs(last[s.pos] - (s.stack + s.bet)) <= EPS for s in hand.seats.values())):
            return {n: last[s.pos] for n, s in hand.seats.items()}
        return {n: s.stack + s.bet for n, s in hand.seats.items()} if preflop else None

    def _round(self, street: int) -> Round:
        """Круг ставок, каким его показывает последний подтверждённый уровень."""
        hand, level = self.hand, self.level
        ring = tuple(sorted(hand.seats))
        bets = {n: s.bet for n, s in hand.seats.items()}
        actor = next((n for n, s in hand.seats.items() if s.pos == level.actor), None)
        return Round(ring, hand.button if hand.button is not None else HERO, self.table.sb, self.table.bb,
                     street, level.pot, bets, {n: s.stack for n, s in hand.seats.items()},
                     frozenset(n for n, s in hand.seats.items() if s.folded),
                     {n: self._invested(n) for n in ring}, *self._anchor(ring, street, bets, actor),
                     actor, list(hand.actions))

    def _anchor(self, ring: tuple[int, ...], street: int, bets: dict[int, float],
                actor: int | None) -> tuple[int, bool]:
        """(откуда идёт круг, походил ли он): последнее повышение; на новой улице — большой блайнд или кнопка."""
        raisers = [a.seat for a in self.hand.actions
                   if a.street == STREETS[street] and a.type in ("bet", "raise")]
        if raisers:
            return raisers[-1], True
        button = self.hand.button if self.hand.button is not None else HERO
        default: tuple[int, bool] = (blinds(ring, button)[1] if street == 0 else button, False)
        if self.hand.exact or actor is None:
            return default
        top = [n for n in ring if bets[n] >= max(bets.values()) - EPS and bets[n] > 0]   # повышение пропущено
        for candidate in top:
            path, seat = [], candidate
            while True:
                path.append(seat)
                if seat == actor:
                    break
                seat = next_seat(ring, seat)
            if not any(s in top for s in path[1:-1]):
                return candidate, True
        return default

    def _shape(self, level: Level) -> tuple:
        """Состояние круга по УРОВНЮ КАДРА — в том же виде, что и состояния поиска истории."""
        seats = {s.pos: n for n, s in self.hand.seats.items()}
        bets = {seats[pos]: s.bet for pos, s in level.seats.items() if pos in seats}
        folded = frozenset(seats[pos] for pos, s in level.seats.items()
                           if pos in seats and _folded(pos, s, level.hero_cards))
        return shape(street_of(len(level.board)), bets, folded, seats.get(level.actor))

    def _goal(self, level: Level, put: dict[int, float] | None, over: bool, pot_before: float,
              via: tuple = ()) -> Goal:
        hand = self.hand
        bets = {n: 0.0 if over else level.seats[s.pos].bet for n, s in hand.seats.items()}
        folded = frozenset(n for n, s in hand.seats.items()
                           if _folded(s.pos, level.seats[s.pos], level.hero_cards))
        actor = None if over else next((n for n, s in hand.seats.items() if s.pos == level.actor), None)
        return Goal(street_of(len(level.board)), bets, put, folded, actor,
                    actor_known=not over and level.actor is not None, over=over,
                    pot_delta=level.pot - pot_before, via=via)

    def _paid(self, n: int) -> float:
        """Сколько место внесло за раздачу по восстановленной истории."""
        return sum(a.amount for a in self.hand.actions if a.seat == n)

    def _invested(self, n: int) -> float:
        """Вложено местом за раздачу к этому моменту (включая ставку на столе)."""
        hand = self.hand
        if hand.start is not None:
            return max(0.0, hand.start[n] - hand.seats[n].stack)
        return self._paid(n)

    def _resync_seats(self, level: Level) -> list[Event]:
        """Кто-то сел или встал посреди руки: состав мест пересобирается, история помечается."""
        hand = self.hand
        known = {s.pos: n for n, s in hand.seats.items()}
        joined = [pos for pos in level.seats if pos not in known]
        left = [pos for pos in known if pos not in level.seats]
        if not joined and not left:
            return []
        for pos in left:
            del hand.seats[known[pos]]
        for pos in joined:
            n = max(hand.seats, default=HERO) + 1
            seen = level.seats[pos]
            hand.seats[n] = _Seat(n, pos, seen.stack, seen.bet, _folded(pos, seen, level.hero_cards),
                                  cards=list(seen.cards) or None)
        self.stats["состав мест изменился посреди руки"] += 1
        hand.start, hand.exact = None, False
        return [Event("resync")]

    def _refresh_over(self, level: Level) -> None:
        """Рука окончена, а кадр изменился (вскрытые карты дорисовались)."""
        for seat in self.hand.seats.values():
            seen = level.seats.get(seat.pos)
            if seen is not None:
                seat.stack = seen.stack
                if seat.pos != "hero":
                    seat.cards = list(seen.cards) or None
        self._snapshot = None

    def _build(self) -> Snapshot:
        hand = self.hand
        over = self.phase == "over"
        seats = [SeatState(seat=n, name=s.pos, stack=round(s.stack, 2),
                           bet_street=0.0 if over else round(s.bet, 2),
                           invested=0.0 if over else round(self._invested(n), 2),
                           folded=s.folded, all_in=s.all_in, is_hero=n == HERO,
                           hole_cards=list(s.cards) if s.cards else None)
                 for n, s in sorted(hand.seats.items())]
        return Snapshot(hand_id=f"h{hand.no:04d}", table_id=self.table_id, sb=self.table.sb, bb=self.table.bb,
                        button_seat=hand.button if hand.button is not None else HERO, actor_seat=HERO,
                        street="showdown" if over else STREETS[street_of(len(hand.board))],
                        board=list(hand.board), pot=round(hand.pot, 2), seats=seats, actions=list(hand.actions))
