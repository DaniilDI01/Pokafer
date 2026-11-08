"""Равномерно случайная стратегия: бейзлайн для тестов и калибровки арены."""
from __future__ import annotations

from typing import ClassVar

from pokafer.brain.strategies.base import Strategy, normalize
from pokafer.core.legal import LegalAction
from pokafer.core.schema import Snapshot


class RandomStrategy(Strategy):
    name: ClassVar[str] = "random"
    label: ClassVar[str] = "равномерная случайная"

    def __init__(self, rng=None):
        pass  # rng не используется, сигнатура единая для всех стратегий

    def distribution(
        self, snap: Snapshot, legal: list[LegalAction]
    ) -> dict[str, float]:
        return normalize({la.key: 1.0 for la in legal})
