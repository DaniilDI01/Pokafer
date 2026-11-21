"""Эквити руки: Монте-Карло против сэмплера диапазона, точный расчёт на ривере.

Модуль не знает про стратегии, платформы и число игроков как таковое:
сэмплер руки оппонента передаётся извне (см. brain/ranges.py). Всё работает
локально, без сети.

Сэмплер диапазона — callable(rng, dead) -> tuple[int, int]: две карты
оппонента, dead — множество занятых карт (наша рука, борд, карты других
оппонентов). Сэмплер обязан выбирать только из живых карт.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from pokafer.core.cards import evaluate7_fast

Sampler = Callable[..., tuple[int, int]]

MIN_ROLLOUTS = 150  # ниже этого выборка слишком шумная для решений


@dataclass
class EquityResult:
    """Эквити руки: доля банка [0..1] с учётом сплитов."""

    equity: float
    n: int  # выбросов (MC) или комбинаций соперника (точный расчёт)
    exact: bool
    elapsed_ms: float
    win: float = 0.0
    tie: float = 0.0
    lose: float = 0.0

    def as_dict(self) -> dict:
        return {
            "equity": round(self.equity, 4),
            "n": self.n,
            "exact": self.exact,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


def _uniform_sampler(rng, dead: set[int]) -> tuple[int, int]:
    live = [c for c in range(52) if c not in dead]
    a, b = rng.sample(live, 2)
    return a, b


def equity_mc(
    hole: list[int],
    board: list[int],
    opponent_samplers: list[Sampler] | None = None,
    n_rollouts: int = 1000,
    rng=None,
    time_budget_ms: float = 120.0,
) -> EquityResult:
    """Эквити методом Монте-Карло: докидываем борд и руки оппонентов.

    opponent_samplers — по одному на живого оппонента; None -> все оппоненты
    со случайными картами. Ограничение по времени: выбирает минимум из
    n_rollouts и того, что успевает за time_budget_ms (но не меньше
    MIN_ROLLOUTS выбросов — для статистической устойчивости).
    """
    import random as _random

    rng = rng or _random.Random()
    hole = list(hole)
    board = list(board)
    if opponent_samplers:
        # None в списке = случайные карты у этого оппонента
        samplers = [s or _uniform_sampler for s in opponent_samplers]
        n_opp = len(samplers)
    else:
        n_opp = 1
        samplers = [_uniform_sampler]
    t0 = time.perf_counter()
    deadline = t0 + time_budget_ms / 1000.0

    dead_base = set(hole) | set(board)
    need_board = 5 - len(board)

    win = tie = lose = 0.0
    n_done = 0
    # проверка времени каждые CHECK_EVERY выбросов (perf_counter дорог)
    CHECK_EVERY = 32
    while True:
        batch = min(CHECK_EVERY, max(MIN_ROLLOUTS - n_done, CHECK_EVERY))
        for _ in range(batch):
            dead = set(dead_base)
            opp_hands = []
            for s in samplers:
                c1, c2 = s(rng, dead)
                dead |= {c1, c2}
                opp_hands.append((c1, c2))
            if need_board:
                live = [c for c in range(52) if c not in dead]
                extra = rng.sample(live, need_board)
                full_board = board + extra
            else:
                full_board = board

            my = evaluate7_fast(hole + full_board)
            best = my
            n_best = 1
            for c1, c2 in opp_hands:
                sc = evaluate7_fast([c1, c2] + full_board)
                if sc > best:
                    best = sc
                    n_best = 1
                elif sc == best:
                    n_best += 1
            if my == best:
                if n_best == 1:
                    win += 1.0  # чистая победа
                else:
                    tie += 1.0 / n_best  # наша доля банка при сплите
            else:
                lose += 1.0
            n_done += 1
        if n_done >= n_rollouts or (
            n_done >= MIN_ROLLOUTS and time.perf_counter() > deadline
        ):
            break

    elapsed = (time.perf_counter() - t0) * 1000.0
    return EquityResult(
        equity=(win + tie) / n_done if n_done else 0.0,
        n=n_done,
        exact=False,
        elapsed_ms=elapsed,
        win=win / n_done,
        tie=tie / n_done,
        lose=lose / n_done,
    )


def equity_exact_river(
    hole: list[int], board: list[int], opponent_combos: list[tuple[int, int]]
) -> EquityResult:
    """Точное эквити на ривере: перечисляем все комбинации рук оппонента.

    board должен быть полным (5 карт). opponent_combos — все возможные руки
    оппонента (диапазон или все C(46,2)); дубликаты отфильтровываются.
    """
    if len(board) != 5:
        raise ValueError("точный расчёт требует полный борд (5 карт)")
    t0 = time.perf_counter()
    dead = set(hole) | set(board)
    combos = []
    seen = set()
    for c1, c2 in opponent_combos:
        if c1 in dead or c2 in dead:
            continue
        key = (min(c1, c2), max(c1, c2))
        if key in seen:
            continue
        seen.add(key)
        combos.append(key)

    win = tie = lose = 0.0
    for c1, c2 in combos:
        opp = evaluate7_fast([c1, c2] + board)
        my = evaluate7_fast(hole + board)
        if my > opp:
            win += 1.0
        elif my == opp:
            tie += 1.0
        else:
            lose += 1.0
    n = len(combos)
    elapsed = (time.perf_counter() - t0) * 1000.0
    return EquityResult(
        equity=(win + tie / 2.0) / n if n else 0.0,
        n=n,
        exact=True,
        elapsed_ms=elapsed,
        win=win / n,
        tie=tie / n,
        lose=lose / n,
    )


def equity_all_combos(hole: list[int], board: list[int]) -> list[tuple[int, int]]:
    """Все руки оппонента из живых карт (для точного расчёта на ривере)."""
    dead = set(hole) | set(board)
    live = [c for c in range(52) if c not in dead]
    out = []
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            out.append((a, b))
    return out


def preflop_class_equity(
    hole: list[int], n_rollouts: int = 2000, rng=None
) -> float:
    """Эквити префлоп-руки против случайной карты соперника (для генератора)."""
    res = equity_mc(
        hole, [], n_rollouts=n_rollouts, rng=rng, time_budget_ms=10_000.0
    )
    return res.equity
