"""Эвристическая стратегия: недо-GTO бейзлайн, который уже:

  - играет смешанно (те же решения в одной ситуации -> разные действия);
  - блефует с осмысленной частотой (чем ниже сила руки и позже позиция,
    тем чаще пробный блеф; на ривере — второй баррель с меньшей частотой);
  - полублефует с дро (флеш-дро/стрит-дро повышают агрессию);
  - считает пот-оддсы для коллов.

Это «живой» соперник для демо и точка отсчёта: GTO-блюпринт и нейросеть
будут менять ровно этот модуль, не трогая остальную систему.
"""
from __future__ import annotations

import random
from typing import ClassVar

from pokafer.brain.strategies.base import Strategy, normalize
from pokafer.core.cards import evaluate7, parse_card, rank, suit
from pokafer.core.legal import LegalAction
from pokafer.core.schema import Snapshot

_CAT_STRENGTH = {
    0: 0.22,  # старшая карта
    1: 0.45,  # пара
    2: 0.63,  # две пары
    3: 0.76,  # сет
    4: 0.86,  # стрит
    5: 0.90,  # флеш
    6: 0.95,  # фулл-хаус
    7: 0.99,  # каре
    8: 0.995,  # роял/стрит-флеш
}


def chen_score(hole: list[int]) -> float:
    """Формула Чена для силы префлоп-рук: AA=20, KK=16, 72o≈-1."""
    r1, r2 = sorted((rank(c) for c in hole), reverse=True)
    pts = {12: 10.0, 11: 8.0, 10: 7.0, 9: 6.0}.get(r1, (r1 + 2) / 2.0)
    if r1 == r2:
        return max(5.0, pts * 2)
    if suit(hole[0]) == suit(hole[1]):
        pts += 2.0
    gap = r1 - r2 - 1
    pts -= {0: 0.0, 1: 1.0, 2: 2.0, 3: 4.0}.get(gap, 5.0)
    if gap <= 1:
        pts += 1.0
        if r2 < 10:  # коннекторы ниже Q слабее
            pts -= 1.0
    return max(-1.0, pts)


def hand_strength(snap: Snapshot) -> tuple[float, str]:
    """Сила руки actor'а в [0,1] + пояснение."""
    me = next(s for s in snap.seats if s.seat == snap.actor_seat)
    hole = [parse_card(c) for c in (me.hole_cards or [])]
    if not hole:
        return 0.5, "карты неизвестны"
    if not snap.board:
        s = min(1.0, max(0.0, (chen_score(hole) + 1.0) / 21.0))
        return s, f"префлоп-сила (Чен) {chen_score(hole):.0f}"
    board = [parse_card(c) for c in snap.board]
    score = evaluate7(hole + board)
    s = _CAT_STRENGTH[score[0]]
    if score[0] == 1:  # качество пары
        s += 0.10 * (score[1] / 12.0)
    s = min(0.99, s)
    draw = _draw_bonus(hole, board, snap.street)
    return min(0.99, s + draw), f"категория {score[0]}, сила {s:.2f}" + (
        f", дро +{draw:.2f}" if draw else ""
    )


def _draw_bonus(hole: list[int], board: list[int], street: str) -> float:
    if street in ("river", "showdown"):
        return 0.0
    bonus = 0.0
    suits = [suit(c) for c in hole + board]
    if any(suits.count(su) == 4 for su in set(suits)):
        bonus += 0.15  # флеш-дро
    ranks = sorted({rank(c) for c in hole + board})
    for lo in range(13):
        window = [r for r in ranks if lo <= r < lo + 5]
        if len(window) == 4:
            bonus += 0.08  # стрит-дро
    return bonus


class HeuristicStrategy(Strategy):
    name: ClassVar[str] = "heuristic"
    label: ClassVar[str] = "эвристика с блефом и пот-оддсами"

    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self._last_note = ""

    def distribution(
        self, snap: Snapshot, legal: list[LegalAction]
    ) -> dict[str, float]:
        by_key = {la.key: la for la in legal}
        s, note = hand_strength(snap)
        self._last_note = note

        me = next(x for x in snap.seats if x.seat == snap.actor_seat)
        call_la = next((la for la in legal if la.action == "call"), None)
        to_call = (call_la.amount_to - me.bet_street) if call_la else 0.0
        call_key = call_la.key if call_la else None
        late = self._is_late_position(snap)
        river = snap.street == "river"

        bets = [la for la in legal if la.action in ("bet", "raise")]
        dist: dict[str, float] = {}

        if to_call <= 1e-9:
            # некому уравнивать: чек / ставка
            if s >= 0.80:
                dist[self._pick_sizing(bets, 2)] = 0.80
                dist["check"] = 0.20
            elif s >= 0.60:
                dist[self._pick_sizing(bets, 1)] = 0.65
                dist["check"] = 0.35
            elif s >= 0.40:
                dist[self._pick_sizing(bets, 0)] = 0.15
                dist["check"] = 0.85
            else:
                bluff = 0.20 + (0.10 if late else 0.0) + (0.08 if "дро" in note else 0.0)
                bluff *= 0.6 if river else 1.0  # второй баррель осторожнее
                dist[self._pick_sizing(bets, 1 if not river else 0)] = min(0.35, bluff)
                dist["check"] = 1.0 - min(0.35, bluff)
        else:
            odds = to_call / (snap.pot + to_call)
            s_eff = s + self.rng.uniform(-0.06, 0.06)
            if s_eff >= 0.85 and bets:
                r = self._pick_sizing(bets, 2)
                dist[r], dist[call_key] = 0.45, 0.55
            elif s_eff >= 0.60:
                dist[call_key] = 0.80
                dist["fold"] = 0.20
            elif s_eff >= max(odds + 0.05, 0.35):
                dist[call_key] = 0.65
                dist["fold"] = 0.35
            else:
                # блеф-катч с малой частотой + изредка блеф-рейз
                dist["fold"] = 0.82
                dist[call_key] = 0.16
                if bets and not river and "дро" in note:
                    dist[self._pick_sizing(bets, 1)] = 0.02

        dist = {k: v for k, v in dist.items() if k in by_key}
        return normalize(dist) if dist else normalize({la.key: 1.0 for la in legal})

    def explain(self, snap, legal, dist) -> str:
        top = max(dist.items(), key=lambda kv: kv[1])
        return f"{self._last_note}; топ-действие {top[0]} ({top[1]:.0%})"

    def _pick_sizing(self, bets: list[LegalAction], tier: int) -> str:
        """tier: 0=min, 1=средний, 2=максимальный из меню."""
        if not bets:
            return "check"
        i = min(len(bets) - 1, max(0, tier + self.rng.choice((-1, 0, 1))))
        return bets[i].key

    @staticmethod
    def _is_late_position(snap: Snapshot) -> bool:
        """Поздняя позиция = actor за кнопкой или это кнопка (упрощённо)."""
        return snap.actor_seat == snap.button_seat
