"""Восстановление действий между двумя подтверждёнными состояниями раздачи.

Между кадрами могут уместиться несколько ходов (быстрый рум, пропуск кадра).
Числа дают только ИТОГ перехода: кто сколько внёс, кто сбросил, чей ход настал.
Порядок и смысл ходов восстанавливает поиск по правилам покера: кто мог
ходить и что ему было можно. «Что было можно» спрашивается у самого мозга
(pokafer.core.legal.legal_actions): правила легальности в проекте одни, и
трекер их не дублирует.

Поиск идёт от ходящего в начале интервала до состояния в конце: фолд, чек,
колл (сумма определена правилами), рейз (сумма — вся оставшаяся разница во
вложениях места; олл-ин, если вложения не видны; или сумма из кадра-
свидетеля). Решение единственное — интервал восстановлен точно. Решений нет
или несколько — трекер отмечает пропуск: выдумывать действия нельзя, уровень
всё равно берётся из кадра.

Свидетели (Goal.via): состояния, увиденные ОДИН раз, ничего не подтверждают,
но сужают объяснения — история обязана пройти через них. Без свидетеля итог
«ставка и два колла» неотличим от «чек — ставка — два колла»: не видно, кто
ставил. Свидетелю, которому противоречит любая история, не верят (сбой).

Честная граница. Если между кадрами место успело внести деньги двумя
порциями («поставил меньше — подняли — докупил»), поиск видит только сумму и
принимает простейшее объяснение: вложения каждого места и итог верны, а
порядок и авторы рейзов могут отличиться. По разбору случаев минимальный рейз
у простейшего объяснения получается не меньше истинного (оно не выдумывает
промежуточных уровней), но это рассуждение, а не доказательство: бою стоит
сверять минимум рейза с экраном. Точная история требует темпа, при котором
каждое действие видно отдельно (на нашем столе — 8 кадров/с).

Чистая логика: пикселей и браузера здесь нет.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pokafer.core.legal import legal_actions
from pokafer.core.schema import ActionRecord, SeatState, Snapshot

EPS = 1e-6
STREETS = ("preflop", "flop", "turn", "river")
NODE_LIMIT = 4000      # стенка от разрастания поиска (~0.1 с на интервал); больше — интервал не восстановлен


def next_seat(ring: Sequence[int], seat: int) -> int:
    """Следующее место по часовой стрелке (ring — места по кругу)."""
    return ring[(ring.index(seat) + 1) % len(ring)]


def clockwise(ring: Sequence[int], start: int) -> list[int]:
    """Все места по часовой стрелке, начиная со следующего за start."""
    i = ring.index(start)
    return [ring[(i + 1 + k) % len(ring)] for k in range(len(ring))]


def blinds(ring: Sequence[int], button: int) -> tuple[int, int]:
    """Малый и большой блайнд по кнопке: в HU кнопка ставит малый, иначе — следующий за кнопкой."""
    small = button if len(ring) == 2 else next_seat(ring, button)
    return small, next_seat(ring, small)


def street_of(board: int) -> int:
    """Улица по числу карт борда: 0 — префлоп, 3 — флоп, 4 — терн, 5 — ривер."""
    return {0: 0, 3: 1, 4: 2, 5: 3}[board]


@dataclass(frozen=True)
class Round:
    """Круг ставок, от которого ищется продолжение (подтверждено кадром или задано правилом)."""

    ring: tuple[int, ...]                 # места по часовой стрелке
    button: int
    sb: float
    bb: float
    street: int
    pot: float
    bets: dict[int, float]
    stacks: dict[int, float]
    folded: frozenset[int]
    invested: dict[int, float]            # вложено за раздачу до начала интервала
    anchor: int                           # откуда идёт круг: последнее повышение или начало улицы
    anchor_acted: bool                    # якорь уже походил (повышение) или ходит последним (начало улицы)
    actor: int | None                     # чей ход; None — не видно
    actions: list[ActionRecord]           # история раздачи до интервала


@dataclass(frozen=True)
class Goal:
    """Состояние в конце интервала — то, что видно в кадре."""

    street: int
    bets: dict[int, float]                # ставки на улице цели (в оконченной руке — нули)
    put: dict[int, float] | None          # сколько каждому внести за интервал; None — не видно (вскрытие)
    folded: frozenset[int]
    actor: int | None                     # чей ход в конце; None — круг или рука закрыты либо ход не виден
    actor_known: bool
    over: bool
    pot_delta: float                      # насколько вырос банк за интервал
    via: tuple = ()                       # состояния, которые история обязана пройти (в порядке)


@dataclass(frozen=True)
class _Options:
    to_call: float                        # сколько добавить, чтобы уровняться (0 — можно чекать)
    raise_to: tuple[float, float] | None  # границы рейза «до»; None — рейз невозможен


@dataclass
class _State:
    street: int
    bets: dict[int, float]
    stacks: dict[int, float]
    folded: set[int]
    acted: set[int]                       # походившие с последнего повышения
    spent: dict[int, float]               # внесено за интервал
    left: dict[int, float] | None         # кому сколько ещё внести (None — не видно)
    actions: list[ActionRecord]
    by_fold: bool = False                 # рука окончилась всеобщим фолдом (непокрытого не возвращают)

    def copy(self) -> "_State":
        return _State(self.street, dict(self.bets), dict(self.stacks), set(self.folded), set(self.acted),
                      dict(self.spent), None if self.left is None else dict(self.left), list(self.actions),
                      self.by_fold)


def shape(street: int, bets: dict[int, float], folded, actor: int | None) -> tuple:
    """Состояние круга без денег: улица, ставки по местам, кто сбросил, чей ход.

    В таком виде сравниваются промежуточные состояния поиска с кадрами,
    увиденными один раз (Goal.via).
    """
    return (street, tuple(sorted((s, round(b, 2)) for s, b in bets.items())), frozenset(folded), actor)


def _options(start: Round, st: _State, seat: int) -> _Options:
    """Что можно делать месту seat: правилами проекта (legal_actions по снапшету состояния поиска)."""
    snap = Snapshot(
        hand_id="search", table_id="search", sb=start.sb, bb=start.bb, button_seat=start.button,
        actor_seat=seat, street=STREETS[st.street], pot=0.0,   # банк на границы рейза не влияет
        seats=[SeatState(seat=s, name=str(s), stack=st.stacks[s], bet_street=st.bets[s],
                         folded=s in st.folded) for s in start.ring],
        actions=st.actions)
    legal = legal_actions(snap)
    call = next((a for a in legal if a.action == "call"), None)
    raises = [a.amount_to for a in legal if a.action in ("bet", "raise")]
    return _Options(call.amount_to - st.bets[seat] if call else 0.0,
                    (raises[0], raises[-1]) if raises else None)


def _answered(ring: Sequence[int], anchor: int, anchor_acted: bool, actor: int,
              eligible: set[int]) -> set[int]:
    """Кто уже походил в этом кругу: активные места по часовой от якоря до ходящего.

    Якорь — последнее повышение (его автор походил) либо начало улицы: большой
    блайнд на префлопе и кнопка постфлоп стоят ПЕРЕД первым ходящим и сами
    ещё не ходили — им отвечать последними.
    """
    done = {anchor} if anchor_acted else set()
    for seat in clockwise(ring, anchor):            # полный круг: ходящим может быть сам якорь
        if seat == actor:
            break
        if seat in eligible:
            done.add(seat)
    return done


def _refund(start: Round, st: _State) -> float:
    """Непокрытая часть наибольшего вложения — вернётся её владельцу на вскрытии."""
    totals = sorted((start.invested[s] + st.spent[s] for s in start.ring if start.invested[s] + st.spent[s] > EPS),
                    reverse=True)
    return max(0.0, totals[0] - totals[1]) if len(totals) > 1 else 0.0


class _Search:
    """Поиск продолжения круга ставок от Round до Goal."""

    def __init__(self, start: Round, goal: Goal):
        self.start, self.goal = start, goal
        self.found: list[list[ActionRecord]] = []
        self.nodes = 0

    def run(self) -> list[ActionRecord] | None:
        s, g = self.start, self.goal
        st = _State(s.street, dict(s.bets), dict(s.stacks), set(s.folded), set(),
                    {seat: 0.0 for seat in s.ring}, None if g.put is None else dict(g.put), list(s.actions))
        eligible = self._can_act(st)
        firsts = [s.actor] if s.actor is not None else [x for x in clockwise(s.ring, s.anchor) if x in eligible]
        for actor in firsts:
            st.acted = _answered(s.ring, s.anchor, s.anchor_acted, actor, eligible)
            self._walk(st, actor, 0)
            if len(self.found) > 1:
                return None                      # два разных объяснения — верить нельзя ничему
        return self.found[0] if len(self.found) == 1 else None

    def _walk(self, st: _State, actor: int | None, seen: int) -> None:
        """Обойти продолжение; seen — сколько состояний из goal.via уже пройдено."""
        if len(self.found) > 1 or self.nodes > NODE_LIMIT:
            return
        self.nodes += 1
        g = self.goal
        if st.street > g.street:
            return                                  # улицу цели миновали — история не сложится
        if seen < len(g.via) and shape(st.street, st.bets, st.folded, actor) == g.via[seen]:
            seen += 1                               # история прошла через увиденный однажды кадр
        if self._reached(st, actor, seen):
            self.found.append(st.actions[len(self.start.actions):])
            return
        if actor is None:
            return
        opts = _options(self.start, st, actor)
        leaving = actor in g.folded              # к концу интервала это место сбросило
        left = None if st.left is None else st.left[actor]
        # место, к концу интервала сбросившее, могло до того и ставить, и коллить
        moves: list[tuple[str, float]] = []
        if opts.to_call > EPS:
            if leaving:
                moves.append(("fold", 0.0))
            if left is None or left >= opts.to_call - EPS:
                moves.append(("call", opts.to_call))
        else:
            moves.append(("check", 0.0))
        # отвечать некому (все остальные олл-ин): рейз от колла отличается лишь
        # возвратом непокрытого — по кадрам одно и то же, берётся колл
        if opts.raise_to is not None and any(st.stacks[o] > EPS for o in self.start.ring
                                             if o != actor and o not in st.folded):
            amounts = []
            if left is None:                     # внесённое не видно (итог руки): олл-ин...
                amounts.append(st.stacks[actor])
            elif left > EPS and st.bets[actor] + left >= opts.raise_to[0] - EPS:
                amounts.append(left)             # ...иначе вся оставшаяся разница в одном рейзе
            seen_to = self._seen_raise(actor, st, seen, opts.raise_to)
            if seen_to is not None:              # ...или сумма, которую видели в кадре-свидетеле
                amounts.append(seen_to - st.bets[actor])
            moves.extend(("raise", add) for add in sorted({round(a, 6) for a in amounts}))
        for kind, add in moves:
            nxt = st.copy()
            if kind == "raise":
                opened = max((nxt.bets[o] for o in self.start.ring if o not in nxt.folded), default=0.0)
                if opened <= EPS:
                    kind = "bet"                 # никто не ставил — это ставка, а не повышение
            nxt.stacks[actor] -= add
            nxt.bets[actor] += add
            nxt.spent[actor] += add
            if nxt.left is not None:
                nxt.left[actor] -= add
            nxt.actions.append(ActionRecord(
                order=len(nxt.actions), street=STREETS[nxt.street], seat=actor, type=kind,
                amount_to=round(nxt.bets[actor], 4) if add else None, amount=round(add, 4)))
            self._walk(nxt, self._after(nxt, actor, kind), seen)

    def _seen_raise(self, actor: int, st: _State, seen: int,
                    bounds: tuple[float, float]) -> float | None:
        """«До» скольких поднял это место в первом неподтверждённом свидетельстве, где его ставка выросла."""
        for _, bets, folded, _ in self.goal.via[seen:]:
            to = dict(bets).get(actor, 0.0)
            if actor in folded:
                return None
            if to > st.bets[actor] + EPS:
                return to if bounds[0] - EPS <= to <= st.bets[actor] + st.stacks[actor] + EPS else None
        return None

    def _after(self, st: _State, actor: int, kind: str) -> int | None:
        """Чей ход после действия; None — круг (или рука) закрыт."""
        if kind == "fold":
            st.folded.add(actor)
        st.acted.add(actor)
        if kind in ("bet", "raise"):
            st.acted = {actor}                   # повышение: остальные должны ответить
        if len(self.start.ring) - len(st.folded) == 1:
            st.by_fold = True                    # все остальные сбросили — рука окончена
            return None
        if set(self._can_act(st)) <= st.acted and len({round(st.bets[s], 6) for s in self._can_act(st)}) <= 1:
            return self._next_street(st)
        return self._next_actor(st, actor)

    def _next_street(self, st: _State) -> int | None:
        if st.street == len(STREETS) - 1:
            return None                          # ривер закрыт — вскрытие
        st.street += 1
        st.bets = {s: 0.0 for s in self.start.ring}
        st.acted = set()
        if len(self._can_act(st)) <= 1:          # ставить больше некому — досдача до вскрытия
            st.street = len(STREETS) - 1
            return None
        return self._next_actor(st, self.start.button)

    def _can_act(self, st: _State) -> set[int]:
        return {s for s in self.start.ring if s not in st.folded and st.stacks[s] > EPS}

    def _next_actor(self, st: _State, from_seat: int) -> int | None:
        for seat in clockwise(self.start.ring, from_seat):
            if seat in self._can_act(st) and seat not in st.acted:
                return seat
        return None

    def _reached(self, st: _State, actor: int | None, seen: int) -> bool:
        g = self.goal
        if seen != len(g.via) or st.street != g.street or st.folded != g.folded:
            return False
        if st.left is not None and any(v > EPS for v in st.left.values()):
            return False
        if g.over:
            refund = 0.0 if st.by_fold else _refund(self.start, st)   # фолды банк не возвращают
            return actor is None and abs(sum(st.spent.values()) - refund - g.pot_delta) <= EPS
        if actor is None or (g.actor_known and actor != g.actor):
            return False
        return all(abs(st.bets[s] - g.bets[s]) <= EPS for s in g.bets)


def plan(start: Round, goal: Goal) -> list[ActionRecord] | None:
    """Действия, переводящие круг start в состояние goal; None — точного объяснения нет."""
    return _Search(start, goal).run()
