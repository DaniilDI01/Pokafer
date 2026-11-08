#!/usr/bin/env python3
"""Арена: мозг A против мозга B, победа в bb/100 (HU NLHE, стек сбрасывается каждую руку).

    python scripts/arena.py --a heuristic --b random --hands 20000

Выводит винрейт A в bb/100 (винрейт B = с обратным знаком), скорость раздач/сек
и 95%-доверительный интервал (дисперсия HU NLHE ~90 bb/100).
"""
from __future__ import annotations

import argparse
import random
import time

from pokafer.brain.api import Brain
from pokafer.core.engine import HandEngine

# эмпирическая дисперсия HU NLHE на 100bb-стеках (bb на 100 рук)
HU_STD_BB100 = 90.0


def run_match(a: Brain, b: Brain, hands: int, sb: float, bb: float, stacks: float, seed: int) -> float:
    """Возвращает суммарный выигрыш A в больших блайндах."""
    rng = random.Random(seed)
    total = 0.0
    for i in range(hands):
        button = 1 if i % 2 == 0 else 2  # чередуем кнопку для честности
        engine = HandEngine(
            seat_stacks={1: stacks, 2: stacks},
            names={1: "A", 2: "B"},
            button_seat=button,
            sb=sb,
            bb=bb,
            seed=rng.randrange(2**31),
            hand_id=f"arena-{i}",
            table_id="arena",
        )
        guard = 0
        while not engine.hand_over:
            guard += 1
            if guard > 300:
                raise RuntimeError(f"рука {i} зациклилась")
            seat = engine.actor_seat
            brain = a if seat == 1 else b
            d = brain.decide(engine.snapshot_for(seat))
            engine.apply(d.action, d.amount_to)
        total += (engine.seats[1].stack - stacks) / bb
    return total


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--a", default="heuristic", help="стратегия места A")
    p.add_argument("--b", default="random", help="стратегия места B")
    p.add_argument("--hands", type=int, default=20000)
    p.add_argument("--stacks", type=float, default=200.0, help="в чипах (200 при bb=2 => 100bb)")
    p.add_argument("--sb", type=float, default=1.0)
    p.add_argument("--bb", type=float, default=2.0)
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()

    a = Brain(args.a, seed=args.seed)
    b = Brain(args.b, seed=args.seed + 1)

    t0 = time.perf_counter()
    total_bb = run_match(a, b, args.hands, args.sb, args.bb, args.stacks, args.seed)
    dt = time.perf_counter() - t0

    wr = total_bb / args.hands * 100.0
    ci = 1.96 * HU_STD_BB100 / (args.hands / 100.0) ** 0.5
    print(f"Матч: {args.a} (A) vs {args.b} (B), HU NLHE {args.stacks / args.bb:.0f}bb")
    print(f"Рук: {args.hands} за {dt:.1f} с ({args.hands / dt:.0f} рук/с)")
    print(f"Винрейт A: {wr:+.1f} bb/100  (95% CI: ±{ci:.1f})")
    print(f"Винрейт B: {-wr:+.1f} bb/100")


if __name__ == "__main__":
    main()
