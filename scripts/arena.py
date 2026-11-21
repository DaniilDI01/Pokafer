#!/usr/bin/env python3
"""Арена: мозг A против мозга B, победа в bb/100 (HU NLHE, стек сбрасывается каждую руку).

    python scripts/arena.py --a heuristic --b random --hands 20000

Выводит винрейт A в bb/100 (винрейт B = с обратным знаком), скорость раздач/сек
и 95%-доверительный интервал по ФАКТИЧЕСКОЙ дисперсии этого матча
(спарринги с «рыбой» устроены заметно тише/шумнее, чем игра двух сильных мозгов).
"""
from __future__ import annotations

import argparse
import math
import random
import time

from pokafer.brain.api import Brain
from pokafer.core.engine import HandEngine

Z = 1.96  # 95%


def run_match(a: Brain, b: Brain, hands: int, sb: float, bb: float, stacks: float, seed: int) -> list[float]:
    """Разыгрывает матч, возвращает выигрыш A за каждую руку (в больших блайндах)."""
    rng = random.Random(seed)
    per_hand: list[float] = []
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
        per_hand.append((engine.seats[1].stack - stacks) / bb)
    return per_hand


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
    per_hand = run_match(a, b, args.hands, args.sb, args.bb, args.stacks, args.seed)
    dt = time.perf_counter() - t0

    n = len(per_hand)
    total_bb = sum(per_hand)
    wr = total_bb / n * 100.0
    if n > 1:
        mean = total_bb / n
        std = math.sqrt(sum((x - mean) ** 2 for x in per_hand) / (n - 1))
        ci = Z * std / math.sqrt(n) * 100.0
    else:
        std = 0.0
        ci = float("nan")
    print(f"Матч: {args.a} (A) vs {args.b} (B), HU NLHE {args.stacks / args.bb:.0f}bb")
    print(f"Рук: {n} за {dt:.1f} с ({n / dt:.0f} рук/с)")
    print(f"Винрейт {args.a} (A): {wr:+.1f} bb/100  (95% CI: ±{ci:.1f}, дисперсия {std:.1f} bb/рука)")
    print(f"Винрейт {args.b} (B): {-wr:+.1f} bb/100")


if __name__ == "__main__":
    main()
