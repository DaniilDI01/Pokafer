"""Память об оппонентах: наблюдаем историю раздач, копим профили.

v1 — процессный in-memory стор, профили считаются на лету через
pokafer.analysis.opponent_stats. Для продакшена интерфейс тот же,
под backs — Redis/файл (см. ROADMAP в README).
"""
from __future__ import annotations

from pokafer.analysis.opponent_stats import stats_from_snapshots
from pokafer.core.schema import Snapshot


class InMemoryProfileStore:
    version = "mem-v0"

    def __init__(self) -> None:
        # последняя (самая полная) версия снапшота по (table_id, hand_id)
        self._hands: dict[tuple[str, str], Snapshot] = {}

    def observe(self, snap: Snapshot) -> None:
        key = (snap.table_id, snap.hand_id)
        prev = self._hands.get(key)
        if prev is None or len(snap.actions) >= len(prev.actions):
            self._hands[key] = snap

    def profiles(self) -> dict[str, dict]:
        """{имя игрока: {vpip, pfr, af, fold_vs_bet, hands}}."""
        return stats_from_snapshots(self._hands.values())

    def profile(self, name: str) -> dict | None:
        return self.profiles().get(name)
