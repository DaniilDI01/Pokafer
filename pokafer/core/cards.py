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


def _straight_high(mask: int) -> int:
    """Старшая карта стрита по битовому маску рангов; 0 если стрита нет."""
    for hi in range(12, 3, -1):
        w = 0b11111 << (hi - 4)
        if mask & w == w:
            return hi
    # колесо A-2-3-4-5: ранги 2,3,4,5 (биты 0..3) + туз (бит 12)
    if mask & 0b1111 == 0b1111 and mask & (1 << 12):
        return 3
    return 0


def evaluate7_fast(cards: list[int]) -> tuple[int, ...]:
    """Быстрый оценщик 5..7 карт: одна проходка вместо 21 комбинации.

    Результат СОВПАДАЕТ с evaluate7 (проверяется тестом) — предназначен
    для Монте-Карло-выбросов, где важна скорость. Категории те же: 8
    стрит-флеш ... 0 старшая карта.
    """
    rank_counts = [0] * 13
    suit_masks = [0, 0, 0, 0]
    for c in cards:
        r, su = divmod(c, 4)
        rank_counts[r] += 1
        suit_masks[su] |= 1 << r

    all_ranks_mask = 0
    for r in range(13):
        if rank_counts[r]:
            all_ranks_mask |= 1 << r

    flush_suit = -1
    for su in range(4):
        m = suit_masks[su]
        if bin(m).count("1") >= 5:
            flush_suit = su
            break

    if flush_suit >= 0:
        fm = suit_masks[flush_suit]
        sf_high = _straight_high(fm)
        if sf_high:
            return (8, sf_high)
        flush_ranks = [
            r for r in range(12, -1, -1) if fm & (1 << r)
        ][:5]
        return (5,) + tuple(flush_ranks)

    # каре / фулл-хаус / тройка / пары — по счётчикам рангов
    quads = [r for r in range(13) if rank_counts[r] == 4]
    trips = [r for r in range(13) if rank_counts[r] == 3]
    pairs = [r for r in range(13) if rank_counts[r] == 2]
    singles = [r for r in range(13) if rank_counts[r] == 1]

    if quads:
        q = quads[0]
        kickers = [r for r in range(12, -1, -1) if r != q and rank_counts[r]]
        return (7, q, kickers[0])

    if trips and (pairs or len(trips) > 1):
        t = trips[-1]
        p = max(pairs[-1] if pairs else -1, trips[0] if len(trips) > 1 else -1)
        return (6, t, p)

    st = _straight_high(all_ranks_mask)
    if st:
        return (4, st)

    if trips:
        t = trips[0]
        kickers = [r for r in range(12, -1, -1) if r != t and rank_counts[r]][:2]
        return (3, t) + tuple(kickers)

    if len(pairs) >= 2:
        p_hi, p_lo = pairs[-1], pairs[-2]
        kickers = [r for r in range(12, -1, -1)
                   if rank_counts[r] and r != p_hi and r != p_lo][:1]
        return (2, p_hi, p_lo) + tuple(kickers)

    if pairs:
        p = pairs[0]
        kickers = [r for r in range(12, -1, -1)
                   if r != p and rank_counts[r]][:3]
        return (1, p) + tuple(kickers)

    return (0,) + tuple(sorted((r for r in range(13) if rank_counts[r]),
                               reverse=True)[:5])


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
