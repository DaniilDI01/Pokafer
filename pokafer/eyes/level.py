"""Уровень кадра: сырые чтения одного Perception -> состояние стола без «мусора».

Глаза выдают сырое («число / неуверенно / пусто»); трекеру нужно одно из двух:
значение, которое можно положить в Snapshot, или «в этом кадре ответа нет».
Здесь сырые чтения переводятся в уровень: ставка без фишки — 0, пустое место —
нет места, чей ход — ровно одно место или никто.

Всё недостоверное — не информация: неуверенное число, «не распознано», два
горящих индикатора разом. Такой кадр получает Level.bad (какое чтение подвело)
и трекер по нему ничего не подтверждает.

Чистая логика: из глаз нужен только тип Perception, пикселей здесь нет.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pokafer.eyes.sites import Preset
from pokafer.eyes.slots import seat_names

if TYPE_CHECKING:  # тип — только для подписей: трекер не тянет в себя пиксели
    from pokafer.eyes.perceive import Perception


@dataclass(frozen=True)
class Table:
    """Что трекер знает о столе сверх кадра: порядок мест, блайнды, индикаторы (пресет)."""

    order: tuple[str, ...]               # места по часовой стрелке от героя; герой — первое
    positions: tuple[str, ...]           # чужие места ([seat.<место>] пресета)
    sb: float
    bb: float
    board_slots: int
    flags: dict[str, tuple[str, ...]]    # вид индикатора -> его зоны (turn / button / over)

    @classmethod
    def from_preset(cls, pref: Preset) -> "Table":
        table = pref.zones.get("table", {})
        order = tuple(table.get("seat_order", ()))
        positions = tuple(seat_names(pref))
        if not order or order[0] != "hero" or not set(positions) <= set(order):
            missing = ", ".join(sorted(set(positions) - set(order))) or "—"
            raise ValueError(f"в пресете {pref.name!r} нет [table].seat_order с героем первым "
                             f"и всеми местами (вне порядка: {missing}) — трекер не может "
                             "нумеровать места")
        flags: dict[str, list[str]] = {}
        for path in pref.zones.get("flags", {}):
            flags.setdefault(path.rsplit(".", 1)[1], []).append(path)
        return cls(order, positions, float(table.get("sb", 0.0)), float(table.get("bb", 0.0)),
                   len(pref.node("board.slots")), {k: tuple(v) for k, v in flags.items()})


@dataclass(frozen=True)
class SeatLevel:
    """Место в одном кадре."""

    pos: str
    stack: float
    bet: float = 0.0                     # ставка улицы; 0 — фишки нет
    cards: tuple[str, ...] = ()          # вскрытые карты места
    in_hand: bool = True                 # карты на месте (скрытые или вскрытые); False — сбросил


@dataclass(frozen=True)
class Level:
    """Состояние стола, прочитанное в одном кадре."""

    hero_cards: tuple[str, ...] = ()     # обе карты героя; () — не читаются (руки нет, сбросил, сбой)
    board: tuple[str, ...] = ()
    pot: float = 0.0
    hero_buttons: bool | None = None     # кнопки действий героя горят; None — зоны нет в пресете
    call: float = 0.0                    # сумма колла на кнопке героя (0 — «Чек»)
    button: str | None = None            # место дилера
    actor: str | None = None             # чей ход; None — никто
    over: bool | None = None             # рука окончена; None — индикатора нет в пресете
    seats: dict[str, SeatLevel] = field(default_factory=dict)   # герой и занятые места
    bad: str | None = None               # первое недостоверное чтение; None — кадр годится


def _owner(path: str) -> str:
    """Владелец индикатора: 'hero.turn' -> hero, 'seat.pos_tl.turn' -> pos_tl."""
    owner = path.rsplit(".", 1)[0]
    return owner[len("seat."):] if owner.startswith("seat.") else owner


def level_of(perception: "Perception", table: Table) -> Level:
    """Сырые чтения кадра -> уровень (с меткой .bad, если чему-то верить нельзя)."""
    bad: str | None = None

    def unreliable(name: str) -> None:
        nonlocal bad
        if bad is None:
            bad = name

    def amount(path: str, hidden):
        """Зона числа -> сумма; hidden — что значит «элемента нет» (None — нет элемента)."""
        reading = perception.numbers.get(path)
        if reading is None or reading.state not in ("number", "empty"):
            unreliable(path)                    # неуверенно, числа нет или зоны нет
            return None
        return reading.value if reading.state == "number" else hidden

    readings = {c.slot.path: c.reading for c in perception.cards}
    hero = [readings.get(f"hero.cards[{i}]") for i in range(2)]
    hero_cards = (tuple(r.label for r in hero)
                  if all(r is not None and r.state == "face" for r in hero) else ())

    board: list[str] = []
    for i in range(table.board_slots):
        reading = readings.get(f"board.slots[{i}]")
        if reading is None:
            continue
        if reading.state == "face" and len(board) == i:
            board.append(reading.label)
        elif reading.state != "empty":
            unreliable(f"board.slots[{i}]")     # не распознано или карта после пустого слота

    pot = amount("pot.amount", hidden=None)
    if pot is None:
        unreliable("pot.amount")
        pot = 0.0

    # кнопки героя: погашены — не его ход; горят — сумма колла или «Чек» (числа нет)
    call = perception.numbers.get("act.call_amount")
    buttons: bool | None = None
    amount_to_call = 0.0
    if call is not None and call.state != "empty":
        if call.state == "low":
            unreliable("act.call_amount")
        else:
            buttons = True
            amount_to_call = call.value or 0.0
    elif call is not None:
        buttons = False

    seats: dict[str, SeatLevel] = {}
    hero_stack = amount("hero.stack", hidden=None)
    if hero_stack is None:
        unreliable("hero.stack")                # у героя всегда есть стек
    else:
        seats["hero"] = SeatLevel("hero", hero_stack, amount("hero.bet", 0.0) or 0.0,
                                  in_hand=bool(hero_cards))
    for pos in table.positions:
        stack = amount(f"seat.{pos}.stack", hidden=None)
        if stack is None:
            continue                            # место пусто (или стек не прочитан — уже отмечено)
        cards = perception.seats.get(pos)
        if cards is None or cards.state == "unclear":
            unreliable(f"seat.{pos}.cards")
            continue
        seats[pos] = SeatLevel(pos, stack, amount(f"seat.{pos}.bet", 0.0) or 0.0,
                               tuple(cards.cards), cards.state != "none")

    def lit(kind: str) -> str | None:
        """У кого горит индикатор вида kind: ровно одно занятое место или никто."""
        owners: list[str] = []
        for path in table.flags.get(kind, ()):
            if _owner(path) not in seats:              # пустое место: его индикаторы не информация
                continue
            reading = perception.flags.get(path)
            if reading is None or reading.state == "unknown":
                unreliable(path)
            elif reading.state == "on":
                owners.append(_owner(path))
        if len(owners) > 1:
            unreliable(f"flags.{kind}")
        return owners[0] if len(owners) == 1 else None

    over = None
    for path in table.flags.get("over", ()):
        reading = perception.flags.get(path)
        if reading is None or reading.state == "unknown":
            unreliable(path)
        else:
            over = reading.state == "on"

    return Level(hero_cards, tuple(board), pot, buttons, amount_to_call,
                 lit("button"), lit("turn"), over, seats, bad)


def key(level: Level, with_hero_cards: bool = True) -> tuple:
    """Всё, по чему кадры сравниваются на «одно и то же состояние».

    Карты героя исключаются, когда трекер их уже запомнил: у сбросившего героя
    они приглушены и не читаются, а рука от этого не меняется.
    """
    seats = tuple((pos, round(s.stack, 2), round(s.bet, 2), s.cards, s.in_hand)
                  for pos, s in sorted(level.seats.items()))
    return ((level.hero_cards,) if with_hero_cards else ()) + (
        level.board, round(level.pot, 2), level.hero_buttons, round(level.call, 2),
        level.button, level.actor, level.over, seats)
