"""Абстракция покера: мост между реальной игрой и обучаемыми таблицами.

Фундамент для двух потребителей:
  - equity-стратегия (классы рук, ранжирование);
  - будущий MCCFR-блюпринт (ключи информационных множеств, бакеты,
    абстрактные размеры ставок).

Модуль чистый и локальный: работает на индексах карт и примитивах
(улица, стек в bb, ключи действий), не знает про Snapshot/платформы.
Артефакт префлоп-ранжирования — генерируемый файл рядом с модулем
(data/preflop_equity.json), читается без записи; если файла нет —
таблица детерминированно считается в памяти.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from pokafer.core.cards import RANK_CHARS, evaluate7_fast, rank, suit
from pokafer.core.equity import preflop_class_equity

DATA_DIR = Path(__file__).resolve().parent / "data"
RANKING_FILE = DATA_DIR / "preflop_equity.json"


# ---------- префлоп-классы рук ----------

@dataclass(frozen=True)
class HandClass:
    """Канонический префлоп-класс: (hi, lo, suited), пары — suited=False."""
    hi: int
    lo: int
    suited: bool

    @property
    def combos(self) -> int:
        if self.hi == self.lo:
            return 6
        return 4 if self.suited else 12

    @property
    def label(self) -> str:
        hi, lo = RANK_CHARS[self.hi], RANK_CHARS[self.lo]
        if self.hi == self.lo:
            return f"{hi}{lo}"
        return f"{hi}{lo}{'s' if self.suited else 'o'}"


def preflop_class(cards: list[int]) -> HandClass:
    """('As','Kd')-подобная пара индексов -> канонический класс."""
    r1, r2 = rank(cards[0]), rank(cards[1])
    hi, lo = max(r1, r2), min(r1, r2)
    suited = suit(cards[0]) == suit(cards[1])
    return HandClass(hi, lo, suited)


def all_hand_classes() -> list[HandClass]:
    """Все 169 классов в каноническом порядке (для идентификаторов)."""
    out = []
    for hi in range(12, -1, -1):
        for lo in range(hi, -1, -1):
            if hi == lo:
                out.append(HandClass(hi, lo, False))
            else:
                out.append(HandClass(hi, lo, True))
                out.append(HandClass(hi, lo, False))
    return out


_HAND_CLASSES = all_hand_classes()
_CLASS_ID = {c: i for i, c in enumerate(_HAND_CLASSES)}


def preflop_class_id(cards: list[int]) -> int:
    return _CLASS_ID[preflop_class(cards)]


def concrete_combos(cls: HandClass, dead: set[int]) -> list[tuple[int, int]]:
    """Конкретные 2-карточные комбинации класса из живых карт."""
    suits = [s for s in range(4)
             if cls.hi * 4 + s not in dead and cls.lo * 4 + s not in dead]
    out = []
    if cls.hi == cls.lo:
        for i, s1 in enumerate(suits):
            for s2 in suits[i + 1:]:
                out.append((cls.hi * 4 + s1, cls.lo * 4 + s2))
    elif cls.suited:
        for s in suits:
            out.append((cls.hi * 4 + s, cls.lo * 4 + s))
    else:
        for s1 in suits:
            for s2 in suits:
                if s1 != s2:
                    out.append((cls.hi * 4 + s1, cls.lo * 4 + s2))
    return out


def class_cards(cls: HandClass) -> list[list[int]]:
    """Все комбинации класса по полной колоде (для генератора таблиц)."""
    return concrete_combos(cls, set())


# ---------- префлоп-ранжирование (артефакт) ----------

@dataclass(frozen=True)
class Ranking:
    """Ранжирование 169 классов: лучший первый, percentile = rank/169."""
    classes: list[HandClass]   # отсортированы по убыванию эквити
    equities: list[float]

    def percentile(self, cls: HandClass) -> float:
        """0.0 = сильнейшая рука (AA), 1.0 = слабейшая."""
        idx = self._index[cls]
        return idx / (len(self.classes) - 1)

    def top_classes(self, width: float) -> list[HandClass]:
        """Топ width доля диапазона (0..1): сильнейшие классы."""
        n = max(1, round(width * len(self.classes)))
        return self.classes[:n]

    _index: dict[HandClass, int] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self._index is None:
            object.__setattr__(
                self, "_index", {c: i for i, c in enumerate(self.classes)}
            )


def build_ranking_in_memory(n_rollouts: int = 2000, seed: int = 7) -> Ranking:
    """Детерминированное ранжирование вычислением (медленно, без записи)."""
    rng = random.Random(seed)
    eqs = []
    for cls in _HAND_CLASSES:
        cards = class_cards(cls)[0]  # эквити не зависит от мастей в классе
        eqs.append(preflop_class_equity(cards, n_rollouts=n_rollouts, rng=rng))
    order = sorted(range(len(_HAND_CLASSES)), key=lambda i: -eqs[i])
    return Ranking(
        classes=[_HAND_CLASSES[i] for i in order],
        equities=[eqs[i] for i in order],
    )


def load_ranking() -> Ranking:
    """Ранжирование из артефакта; если файла нет — считаем в памяти."""
    global _RANKING_CACHE
    if _RANKING_CACHE is not None:
        return _RANKING_CACHE
    if RANKING_FILE.exists():
        data = json.loads(RANKING_FILE.read_text(encoding="utf-8"))
        classes = [HandClass(*e["class"]) for e in data["ranking"]]
        _RANKING_CACHE = Ranking(
            classes=classes, equities=[e["eq"] for e in data["ranking"]]
        )
    else:
        _RANKING_CACHE = build_ranking_in_memory()
    return _RANKING_CACHE


_RANKING_CACHE: Ranking | None = None


# ---------- бакеты (для ключей блюпринта) ----------

STACK_EDGES = (0, 8, 15, 25, 40, 60, 90, float("inf"))


def stack_bucket(stack_bb: float) -> int:
    """Индекс диапазона стека в больших блайндах (0..6)."""
    for i in range(len(STACK_EDGES) - 1):
        if STACK_EDGES[i] <= stack_bb < STACK_EDGES[i + 1]:
            return i
    return 0


_CAT_BASE = {8: 1.00, 7: 0.97, 6: 0.93, 5: 0.88, 4: 0.82, 3: 0.74, 2: 0.62, 1: 0.42, 0: 0.10}


def hand_strength(hole: list[int], board: list[int]) -> float:
    """Быстрая аналитическая сила руки [0..1]: комбинация + кикеры + дро.

    Для бакетов абстракции; калибруется против MC-эквити тестом.
    """
    n_board = len(board)
    score = evaluate7_fast(hole + board)
    cat = score[0]
    s = _CAT_BASE[cat]
    if cat in (1, 2, 3):
        s += 0.10 * (score[1] / 12.0)  # качество пары/сета
    if cat == 0 and score[1:]:
        s += 0.04 * (score[1] / 12.0)
    if n_board < 5:  # потенциал дро (кроме ривера и уже собранных комбинаций)
        if cat < 5:
            suits = [suit(c) for c in hole + board]
            if max(suits.count(x) for x in set(suits)) == 4:
                s += 0.09  # флеш-дро
        if cat < 4:
            ranks_mask = 0
            for c in hole + board:
                ranks_mask |= 1 << rank(c)
            for lo in range(9):
                w = 0b11111 << lo
                if ranks_mask & w == w:
                    continue
                window = bin(ranks_mask & w).count("1")
                if window >= 4:
                    s += 0.05  # стрит-дро (гатшот и сильнее)
                    break
    return min(1.0, s)


def hand_bucket(hole: list[int], board: list[int], n_buckets: int = 40) -> int:
    """Бакет силы руки 0..n_buckets-1 (для ключей информационных множеств)."""
    return min(n_buckets - 1, int(hand_strength(hole, board) * n_buckets))


# ---------- абстрактные действия (общие для CFR и рантайма) ----------

ABSTRACT_BET_FRACTIONS = (0.5, 1.0, 2.0)  # доли банка после колла; 3 = олл-ин
ACTION_CODES = {"fold": "f", "check": "k", "call": "c"}


def encode_action(action: str, bet_index: int | None = None) -> str:
    """Действие -> код для ключа: 'f','k','c','b0'..'b2','b3'(олл-ин),'r0'..."""
    if action in ACTION_CODES:
        return ACTION_CODES[action]
    if action in ("bet", "raise"):
        return f"{'b' if action == 'bet' else 'r'}{bet_index if bet_index is not None else 3}"
    raise ValueError(f"неизвестное действие {action!r}")


def abstract_bet_index(amount_increment: float, pot_after_call: float,
                       is_all_in: bool) -> int:
    """Прибавка ставки -> индекс абстрактного размера (доля банка после колла)."""
    if is_all_in:
        return len(ABSTRACT_BET_FRACTIONS)
    if pot_after_call <= 1e-9:
        return 0
    frac = amount_increment / pot_after_call
    best = min(range(len(ABSTRACT_BET_FRACTIONS)),
               key=lambda i: abs(ABSTRACT_BET_FRACTIONS[i] - frac))
    return best


def nearest_bet_amount(bet_index: int, pot_after_call: float, max_bet: float,
                       min_to: float, max_to: float) -> float:
    """Абстрактный индекс -> сумма «до»: max_bet + доля банка после колла.

    Для ставки max_bet=0, поэтому формула едина для bet и raise.
    """
    if bet_index >= len(ABSTRACT_BET_FRACTIONS):
        return max_to
    frac = ABSTRACT_BET_FRACTIONS[bet_index]
    target = max_bet + frac * pot_after_call
    return min(max(target, min_to), max_to)


def infoset_key(street: str, pf_class: int, bucket: int, stack_b: int,
                action_codes: tuple[str, ...]) -> str:
    """Канонический ключ информационного множества (для таблиц регретов)."""
    return f"{street}|p{pf_class}|h{bucket}|s{stack_b}|a{'.'.join(action_codes)}"
