"""Синтетическая «рыба» микролимитов: модель популяции NL2–NL10.

По данным литературы и трекеров типичный игрок микролимитов:
  - лузово-пассивный: входит и коллирует слишком широко;
  - агрессия почти только с готовой силой (ставка = сила);
  - редко блефует и почти не рейзит без монстра.

Используется как «спарринг-популяция» для калибровки эксплойт-стратегий,
пока нет клонов реальных игроков из собранных данных. ВАЖНО: это наша
модель популяции, а не сама популяция — после сбора реальных HH
синтетику заменят/калибруют клоны (pokafer.analysis + behavioral cloning).
"""
from __future__ import annotations

import random
from typing import ClassVar

from pokafer.brain.strategies.base import Strategy, normalize
from pokafer.brain.strategies.heuristic import hand_strength
from pokafer.core.legal import LegalAction
from pokafer.core.schema import Snapshot


class FishStrategy(Strategy):
    name: ClassVar[str] = "fish"
    label: ClassVar[str] = "синтетическая рыба NL2-10 (лузово-пассивная)"

    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self._last_note = ""

    def distribution(
        self, snap: Snapshot, legal: list[LegalAction]
    ) -> dict[str, float]:
        by_key = {la.key: la for la in legal}
        s, note = hand_strength(snap)
        self._last_note = note

        call_la = next((la for la in legal if la.action == "call"), None)
        to_call = call_la is not None
        call_key = call_la.key if call_la else None
        bets = [la for la in legal if la.action in ("bet", "raise")]
        dist: dict[str, float] = {}

        if not to_call:
            # без ставки: почти всегда чек; ставит только с силой, минимально
            if s >= 0.78:
                dist[self._min_sizing(bets)] = 0.55
                dist["check"] = 0.45
            elif s >= 0.55:
                dist[self._min_sizing(bets)] = 0.20
                dist["check"] = 0.80
            else:
                dist["check"] = 1.0
        else:
            # против ставки: станция — коллирует очень широко, рейз только с монстром
            if s >= 0.88 and bets:
                dist[self._min_sizing(bets)] = 0.30
                dist[call_key] = 0.70
            elif s >= 0.30:
                dist[call_key] = 0.85
                dist["fold"] = 0.15
            else:
                # даже мусор иногда коллит («любопытство») — иначе не рыба
                dist[call_key] = 0.40
                dist["fold"] = 0.60

        dist = {k: v for k, v in dist.items() if k in by_key}
        return normalize(dist) if dist else normalize({la.key: 1.0 for la in legal})

    def explain(self, snap, legal, dist) -> str:
        top = max(dist.items(), key=lambda kv: kv[1])
        return f"(рыба) {self._last_note}; топ {top[0]} ({top[1]:.0%})"

    @staticmethod
    def _min_sizing(bets: list[LegalAction]) -> str:
        return bets[0].key if bets else "check"
