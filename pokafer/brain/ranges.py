"""Моделирование диапазонов оппонентов: Snapshot + профили -> сэмплер рук.

Чистый модуль: не хранит состояния, не пишет файлы, число игроков берёт
из Snapshot. Используется equity-стратегией; блюпринту (CFR) не нужен —
там диапазон соперника кодируется линией раздачи в ключе информационного
множества.

Модель диапазона: «топ N% префлоп-ранжирования, суженный линией раздачи».
N — по позиции (чем позже говорил — тем шире), линии (рейз сужает сильнее
колла) и накопленной статистике (VPIP), если платформа передала профили.
"""
from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

from pokafer.core.abstraction import (
    HandClass,
    concrete_combos,
    load_ranking,
    preflop_class,
)
from pokafer.core.cards import evaluate7_fast  # noqa: F401 (используется тестами)
from pokafer.core.schema import Snapshot

Sampler = Callable[[random.Random, set[int]], tuple[int, int]]

# ---------- константы модели (принципиальные, не «крутилки») ----------

W_UTG = 0.16          # ширина диапазона открытия с ранней позиции
W_BTN = 0.48          # с баттона
W_CALL = 1.6          # коллер после рейза шире опенера
W_LIMPER = 0.45       # лимпер в мертвый банк
W_BB_OPTION = 0.90    # чек большой блайнды (защита почти всего)
NARROW_BET = 0.60     # ставка постфлоп сужает диапазон до этой доли
NARROW_RAISE = 0.42   # рейз постфлоп сужает сильнее
NARROW_CALL = 0.88    # колл сужает слабо (коллируют и дро)
MULTI_RAISE = 0.70    # каждый дополнительный префлоп-рейз сужает pot
VPOP_DEFAULT = 0.35   # типичный VPIP популяции (для правки по профилю)
PROFILE_MIN_HANDS = 5  # статистика доверяется от этого числа рук


@dataclass
class OpponentRange:
    """Диапазон одного оппонента: классы рук + сэмплер конкретных карт."""

    classes: list[HandClass]
    rng_state: random.Random | None = None

    def _weights(self) -> list[float]:
        return [float(c.combos) for c in self.classes]

    def sampler(self, rng: random.Random) -> Sampler:
        """Сэмплер для core.equity: (rng, dead) -> 2 карты оппонента."""
        classes = list(self.classes)
        weights = self._weights()

        def sample(r: random.Random, dead: set[int]) -> tuple[int, int]:
            while classes:
                cls = r.choices(classes, weights=weights, k=1)[0]
                combos = concrete_combos(cls, dead)
                if combos:
                    return r.choice(combos)
                # все комбо класса мертвы — исключаем и пробуем снова
                idx = classes.index(cls)
                classes.pop(idx)
                weights.pop(idx)
            # диапазон исчерпан (экстремально много занятых карт) — равномерно
            live = [c for c in range(52) if c not in dead]
            a, b = r.sample(live, 2)
            return a, b

        return sample

    def combos(self, dead: set[int]) -> list[tuple[int, int]]:
        """Все конкретные комбинации диапазона (для точного ривера)."""
        out: list[tuple[int, int]] = []
        seen = set()
        for cls in self.classes:
            for c1, c2 in concrete_combos(cls, dead):
                key = (min(c1, c2), max(c1, c2))
                if key not in seen:
                    seen.add(key)
                    out.append(key)
        return out


def preflop_order(snap: Snapshot) -> list[int]:
    """Порядок хода префлоп (первый ходит первым): HU — кнопка/BB."""
    seats = [s.seat for s in snap.seats]
    n = len(seats)
    btn = snap.button_seat
    if n == 2:
        return [btn, next(s for s in seats if s != btn)]
    order = []
    cur = btn
    for _ in range(n):
        cur = seats[(seats.index(cur) + 1) % n]
        order.append(cur)
    return order[2:] + order[:2]  # после BB: UTG, ..., кнопка, SB, BB


def position_fraction(snap: Snapshot, seat: int) -> float:
    """Насколько поздно место говорит префлоп: 0 = первым (UTG), 1 = кнопка.

    Кнопка — самая поздняя позиция, она всегда 1 (в HU неблайндовых мест
    нет: кнопка/SB — 0, BB — 1). Остальные места — доля своего пути в
    порядке хода. Блайнды ходят после кнопки, но это им не выгоднее: малый
    блайнд получается «на шаг у́же кнопки» (в 6-макс 0,8) — постфлоп он без
    позиции — и открывается у́же неё, как и должно.

    Прежде шкала шла от UTG до большого блайнда, и кнопка получала не 1,
    а (n-3)/(n-1): в 6-макс она открывалась у́же малого блайнда (35% против
    42%), а параметр «доля рук на кнопке» доставался большому блайнду.
    """
    order = preflop_order(snap)
    if len(order) > 2 and seat == snap.button_seat:
        return 1.0
    return order.index(seat) / max(len(order) - 1, 1)


def _seat_actions(snap: Snapshot, seat: int) -> list:
    return [a for a in snap.actions if a.seat == seat]


def _preflop_line(snap: Snapshot, seat: int) -> str:
    """Что делал оппонент префлоп: 'raise' | 'call' | 'option' | 'none'."""
    acts = [a for a in _seat_actions(snap, seat)
            if a.street == "preflop" and a.type != "post"]
    if not acts:
        return "none"
    if any(a.type in ("bet", "raise") for a in acts):
        return "raise"
    faced = [a for a in snap.actions
             if a.street == "preflop" and a.type in ("bet", "raise")]
    return "option" if not faced else "call"


def _base_width(snap: Snapshot, seat: int, line: str) -> float:
    """Ширина диапазона до правок по профилю и постфлоп-линии."""
    pos = position_fraction(snap, seat)
    if line == "raise":
        width = W_UTG + (W_BTN - W_UTG) * pos
    elif line == "call":
        width = (W_UTG + (W_BTN - W_UTG) * pos) * W_CALL
    elif line == "option":
        width = W_BB_OPTION
    else:  # лимп / без явных действий
        width = W_LIMPER
    n_raises = sum(
        1 for a in snap.actions
        if a.street == "preflop" and a.type in ("bet", "raise")
    )
    width *= MULTI_RAISE ** max(0, n_raises - 1)
    me = next(s for s in snap.seats if s.seat == seat)
    bb = snap.bb
    if me.stack / bb <= 12:  # короткий стек: пушает шире
        width = max(width, 0.30)
    return min(width, 0.95)


def _apply_profile(width: float, profile: dict | None) -> float:
    """Правка ширины по наблюдаемому VPIP (соперник лузовее/тайтовее популяции)."""
    if not profile or profile.get("hands", 0) < PROFILE_MIN_HANDS:
        return width
    vpip = float(profile.get("vpip", VPOP_DEFAULT))
    return min(max(width * (vpip / VPOP_DEFAULT), 0.04), 0.95)


def _apply_postflop_line(snap: Snapshot, seat: int, width: float) -> float:
    """Сужение диапазона по агрессии оппонента постфлоп."""
    for a in _seat_actions(snap, seat):
        if a.street == "preflop":
            continue
        if a.type in ("bet", "raise"):
            width *= NARROW_RAISE if a.type == "raise" else NARROW_BET
        elif a.type == "call":
            width *= NARROW_CALL
    return min(width, 0.95)


def build_range(snap: Snapshot, opp_seat: int,
                profile: dict | None = None) -> OpponentRange:
    """Диапазон одного оппонента: позиция + линия + стек + (опц.) профиль."""
    line = _preflop_line(snap, opp_seat)
    width = _base_width(snap, opp_seat, line)
    width = _apply_profile(width, profile)
    width = _apply_postflop_line(snap, opp_seat, width)

    ranking = load_ranking()
    classes = ranking.top_classes(width)
    if not classes:
        classes = [ranking.classes[0]]
    return OpponentRange(classes=classes)


def ranges_for_opponents(snap: Snapshot, hero_seat: int,
                         profiles: dict[str, dict] | None = None
                         ) -> dict[int, OpponentRange]:
    """Диапазоны всех живых оппонентов hero_seat (сбросившие исключены)."""
    profiles = profiles or {}
    name_of = {s.seat: s.name for s in snap.seats}
    out = {}
    for s in snap.seats:
        if s.seat == hero_seat or s.folded:
            continue
        out[s.seat] = build_range(snap, s.seat, profiles.get(name_of[s.seat]))
    return out
