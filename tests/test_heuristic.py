import random

from pokafer.brain.strategies.heuristic import HeuristicStrategy
from pokafer.core.legal import legal_actions


def _flop_snapshot(hole: list[str], board: list[str]) -> "Snapshot":
    from pokafer.core.schema import SeatState, Snapshot

    return Snapshot(
        hand_id="h1", sb=1, bb=2, button_seat=1, actor_seat=2,
        street="flop", board=board, pot=6.0,
        seats=[
            SeatState(seat=1, name="opp", stack=197.0, bet_street=0, invested=3.0),
            SeatState(seat=2, name="hero", stack=197.0, bet_street=0, invested=3.0,
                      is_hero=True, hole_cards=hole),
        ],
    )


def test_quads_bet_heavily():
    snap = _flop_snapshot(["7h", "7d"], ["7s", "7c", "Kd"])
    legal = legal_actions(snap)
    dist = HeuristicStrategy(rng=random.Random(3)).distribution(snap, legal)

    assert set(dist) <= {la.key for la in legal}
    assert abs(sum(dist.values()) - 1.0) < 1e-6
    bet_prob = sum(v for k, v in dist.items() if k.startswith(("bet", "raise")))
    assert bet_prob > 0.6  # каре -> ставка


def test_trash_checks_often_but_bluff_present():
    snap = _flop_snapshot(["3h", "2d"], ["Ks", "Qc", "Jd"])
    legal = legal_actions(snap)
    dist = HeuristicStrategy(rng=random.Random(5)).distribution(snap, legal)

    assert set(dist) <= {la.key for la in legal}
    assert dist.get("check", 0.0) > 0.5  # мусор чаще чекает...
    # ...но в той же ситуации блеф имеет положительный вес:
    # одна и та же рука -> разные действия (смешанная стратегия)
    assert any(k.startswith("bet") and v > 0.05 for k, v in dist.items())
