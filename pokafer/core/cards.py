"""Карты: парсинг строк ('As', 'Td') -> индексы 0..51 и оценка комбинаций.

Индекс карты: rank_idx * 4 + suit_idx, где rank 0..12 = 2..9,T,J,Q,K,A,
suit 0..3 = s,h,d,c. Старшинство определяется рангом.
"""
from __future__ import annotations

from itertools import combinations

RANK_CHARS = "23456789TJQKA"
SUIT_CHARS = "shdc"

# Категории комбинаций: больше — сильнее
CATEGORY_NAMES = {
    8: "straight flush",
    7: "four of a kind",
    6: "full house",
    5: "flush",
    4: "straight",
    3: "three of a kind",
    2: "two pair",
    1: "one pair",
    0: "high card",
}


def parse_card(s: str) -> int:
    """'As' -> индекс. Ранги 2-9/T/J/Q/K/A, масти s/h/d/c."""
    s = s.strip()
    if len(s) != 2:
        raise ValueError(f"Карта должна быть вида 'As': {s!r}")
    r = s[0].upper()
    u = s[1].lower()
    if r not in RANK_CHARS or u not in SUIT_CHARS:
        raise ValueError(f"Неизвестная карта: {s!r}")
    return RANK_CHARS.index(r) * 4 + SUIT_CHARS.index(u)


def card_str(c: int) -> str:
    return RANK_CHARS[c // 4] + SUIT_CHARS[c % 4]


def rank(c: int) -> int:
    return c // 4


def suit(c: int) -> int:
    return c % 4


def parse_cards(cards: list[str]) -> list[int]:
    return [parse_card(c) for c in cards]


def _evaluate5(cs: tuple[int, ...]) -> tuple[int, ...]:
    """Оценка ровно 5 карт -> сортируемый кортеж (категория, киккеры...)."""
    rs = sorted((rank(c) for c in cs), reverse=True)
    is_flush = len({suit(c) for c in cs}) == 1

    uniq = sorted(set(rs), reverse=True)
    straight_high = 0
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            straight_high = uniq[0]
        elif uniq == [12, 3, 2, 1, 0]:  # колесо A-2-3-4-5 (туз снизу)
            straight_high = 3

    counts: dict[int, int] = {}
    for r in rs:
        counts[r] = counts.get(r, 0) + 1
    groups = sorted(counts.items(), key=lambda kv: (-kv[1], -kv[0]))

    if is_flush and straight_high:
        return (8, straight_high)
    if groups[0][1] == 4:
        return (7, groups[0][0], groups[1][0])
    if groups[0][1] == 3 and groups[1][1] == 2:
        return (6, groups[0][0], groups[1][0])
    if is_flush:
        return (5,) + tuple(rs)
    if straight_high:
        return (4, straight_high)
    if groups[0][1] == 3:
        kickers = [r for r in rs if r != groups[0][0]]
        return (3, groups[0][0]) + tuple(kickers)
    if groups[0][1] == 2 and groups[1][1] == 2:
        kicker = [r for r in rs if counts[r] == 1][0]
        return (2, groups[0][0], groups[1][0], kicker)
    if groups[0][1] == 2:
        kickers = [r for r in rs if counts[r] == 1]
        return (1, groups[0][0]) + tuple(kickers)
    return (0,) + tuple(rs)


def evaluate7(cards: list[int]) -> tuple[int, ...]:
    """Лучшая комбинация из 5..7 карт. Больше кортеж — сильнее."""
    if not 5 <= len(cards) <= 7:
        raise ValueError(f"evaluate7 ожидает 5-7 карт, получено {len(cards)}")
    return max(_evaluate5(c) for c in combinations(cards, 5))


def hand_name(score: tuple[int, ...]) -> str:
    """Человекочитаемое имя комбинации."""
    cat = score[0]
    base = CATEGORY_NAMES[cat]
    if cat in (4, 8):
        return f"{base}, {RANK_CHARS[score[1]]} high"
    if cat == 6:
        return f"{base}, {RANK_CHARS[score[1]]} full of {RANK_CHARS[score[2]]}"
    if cat == 7:
        return f"{base}, {RANK_CHARS[score[1]]}s"
    if cat == 2:
        return f"{base}, {RANK_CHARS[score[1]]}s and {RANK_CHARS[score[2]]}s"
    if cat in (1, 3):
        return f"{base}, {RANK_CHARS[score[1]]}s"
    return base


_RU_CATEGORY = {
    8: "стрит-флеш",
    7: "каре",
    6: "фулл-хаус",
    5: "флеш",
    4: "стрит",
    3: "тройка",
    2: "две пары",
    1: "пара",
    0: "старшая карта",
}


def hand_name_ru(score: tuple[int, ...]) -> str:
    """Русское имя комбинации: 'пара J', 'две пары: J и 3', 'флеш до A'."""
    cat = score[0]
    if cat in (4, 8, 5):
        return f"{_RU_CATEGORY[cat]} до {RANK_CHARS[score[1]]}"
    if cat == 6:
        return f"фулл-хаус: {RANK_CHARS[score[1]]} на {RANK_CHARS[score[2]]}"
    if cat == 7:
        return f"каре {RANK_CHARS[score[1]]}"
    if cat == 2:
        return f"две пары: {RANK_CHARS[score[1]]} и {RANK_CHARS[score[2]]}"
    if cat in (1, 3):
        return f"{_RU_CATEGORY[cat]} {RANK_CHARS[score[1]]}"
    return f"старшая {RANK_CHARS[score[1]]}"
