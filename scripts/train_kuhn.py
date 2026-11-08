#!/usr/bin/env python3
"""Обучить CFR на покере Куна: равновесие + мера близости к нему.

    python scripts/train_kuhn.py --iterations 30000

Демонстрирует главное утверждение проекта: блеф — не хардкод, а вычисленное
свойство равновесия (P1 обязан блефовать валетом ~1/3 рук).
"""
import argparse

from pokafer.research.kuhn_cfr import KuhnCFR


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iterations", type=int, default=30000)
    p.add_argument("--seed", type=int, default=17)
    args = p.parse_args()

    solver = KuhnCFR(iterations=args.iterations, seed=args.seed)
    expl = solver.exploitability()
    print(f"Итераций: {args.iterations}")
    print(f"Exploitability (0 = идеальное равновесие): {expl:.4f}")
    print(f"Цена игры для P1 (теория: -1/18 = {-1/18:.4f})")

    for player, label in ((0, "P1"), (1, "P2")):
        print(f"\n{label}, средняя стратегия (bet = ставка/колл):")
        for info, s in solver.average_strategy(player).items():
            print(f"  {info:10s} bet {s['b']:.3f} | pass {s['p']:.3f}")

    print(
        "\nСмотрите: с валетом (J) P1 ставит ~1/3 — это блеф из равновесия,\n"
        "с королём (K) — почти всегда. Никто это не программировал вручную."
    )


if __name__ == "__main__":
    main()
