"""Общий интерфейс источника данных: адаптер отдаёт поток Snapshot'ов.

Мозгу всё равно, откуда снапшоты, — пока они соответствуют схеме
pokafer.core.schema.Snapshot. Режимы:
  офлайн   — итератор по снапшотам (реплей исторических логов);
  реальное время — колбэк on_snapshot при событиях стола.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from pokafer.core.schema import Snapshot


class OfflineSource(Protocol):
    def snapshots(self) -> Iterator[Snapshot]: ...


class LiveSource(Protocol):
    def subscribe(self, on_snapshot) -> None: ...
