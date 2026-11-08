"""Базовый интерфейс стратегии: распределение вероятностей по легальным действиям.

Стратегия возвращает смешанную стратегию (dict key -> вероятность), а не одно
действие — это фундаментально для покера: непредсказуемость и блеф живут здесь.
Сэмплирование конкретного действия делает Brain.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from pokafer.core.legal import LegalAction
from pokafer.core.schema import Snapshot


class Strategy(ABC):
    name: ClassVar[str] = "abstract"
    label: ClassVar[str] = "абстрактная стратегия"

    @abstractmethod
    def distribution(
        self, snap: Snapshot, legal: list[LegalAction]
    ) -> dict[str, float]:
        """Смешанная стратегия: {action_key: вероятность}. Сумма ~1."""

    def explain(
        self, snap: Snapshot, legal: list[LegalAction], dist: dict[str, float]
    ) -> str | None:
        """Короткое человекочитаемое пояснение (для демо и отчётов)."""
        return None


def normalize(dist: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, w) for w in dist.values())
    if total <= 1e-12:
        n = len(dist)
        return {k: 1.0 / n for k in dist} if n else {}
    return {k: max(0.0, w) / total for k, w in dist.items()}
