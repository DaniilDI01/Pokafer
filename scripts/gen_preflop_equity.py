#!/usr/bin/env python3
"""Генератор артефакта: эквити 169 префлоп-классов против случайной руки.

    python scripts/gen_preflop_equity.py [--rollouts 2000] [--seed 7]

Пишет pokafer/core/data/preflop_equity.json — генерируемый артефакт,
коммитится в репо (см. docs/DECISIONS.md: локальность, без runtime-кэшей).
Стратегии читают его только на чтение через core.abstraction.load_ranking().
"""
from __future__ import annotations

import argparse
import json
import time

from pokafer.core.abstraction import (
    DATA_DIR,
    RANKING_FILE,
    build_ranking_in_memory,
)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rollouts", type=int, default=2000,
                   help="выбросов Монте-Карло на класс (точность ~0.01)")
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()

    t0 = time.perf_counter()
    ranking = build_ranking_in_memory(n_rollouts=args.rollouts, seed=args.seed)
    dt = time.perf_counter() - t0

    entries = []
    for cls, eq in zip(ranking.classes, ranking.equities):
        entries.append({
            "class": [cls.hi, cls.lo, int(cls.suited)],
            "label": cls.label,
            "eq": round(eq, 4),
            "combos": cls.combos,
        })

    payload = {
        "meta": {
            "generator": "scripts/gen_preflop_equity.py",
            "seed": args.seed,
            "rollouts_per_class": args.rollouts,
            "opponent": "uniform random hand",
            "seconds": round(dt, 1),
        },
        "ranking": entries,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RANKING_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    best = entries[0]
    worst = entries[-1]
    print(f"готово за {dt:.0f} с -> {RANKING_FILE}")
    print(f"  лучший: {best['label']} eq={best['eq']}, худший: {worst['label']} eq={worst['eq']}")


if __name__ == "__main__":
    main()
