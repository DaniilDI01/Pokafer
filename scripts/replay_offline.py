#!/usr/bin/env python3
"""Прогнать мозг по историческим снапшотам (JSONL) и получить сводку.

    python scripts/replay_offline.py data/snapshots/demo.jsonl

Источник JSONL — demo_cli.py --record или (позже) конвертеры румов.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from pokafer.adapters.replay_file import ReplayFile
from pokafer.analysis import stats_from_snapshots
from pokafer.brain.api import Brain


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("path", type=Path)
    p.add_argument("--strategy", default="heuristic")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    replay = ReplayFile(args.path)
    brain = Brain(args.strategy, seed=args.seed)

    counts: Counter[str] = Counter()
    elapsed: list[float] = []
    confidence: list[float] = []
    snaps = []
    for snap in replay.snapshots():
        snaps.append(snap)
        d = brain.decide(snap)
        counts[d.key().split(":")[0]] += 1
        elapsed.append(d.elapsed_ms)
        confidence.append(max(d.distribution.values(), default=0.0))

    if not counts:
        print("Снапшотов не найдено")
        return

    n = len(elapsed)
    print(f"Снапшотов: {n}, стратегия: {args.strategy}")
    print("Действия:", dict(counts.most_common()))
    print(f"Время решения: среднее {sum(elapsed)/n:.2f} мс, максимум {max(elapsed):.2f} мс")
    print(f"Уверенность (макс. вероятность): средняя {sum(confidence)/n:.2f}")

    print("\nПрофили игроков (из этих же данных):")
    for name, s in stats_from_snapshots(snaps).items():
        print(
            f"  {name}: {s['hands']} рук, VPIP {s['vpip']:.0%}, PFR {s['pfr']:.0%}, "
            f"AF {s['af']}, fold-vs-bet {s['fold_vs_bet']:.0%}"
        )


if __name__ == "__main__":
    main()
