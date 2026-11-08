import random

from pokafer.brain.strategies.fish import FishStrategy
from pokafer.brain.strategies.heuristic import HeuristicStrategy
from pokafer.brain.strategies.micro import MicroExploitStrategy
from pokafer.core.legal import legal_actions
from pokafer.core.schema import SeatState, Snapshot


def _flop_snapshot(hole: list[str], board: list[str], hero_bet: float = 0.0,
                   opp_bet: float = 0.0) -> Snapshot:
    pot = 6.0 + hero_bet + opp_bet
    return Snapshot(
        hand_id="h1", sb=1, bb=2, button_seat=1, actor_seat=2,
        street="flop", board=board, pot=pot,
        seats=[
            SeatState(seat=1, name="opp", stack=197.0, bet_street=opp_bet,
                      invested=3.0 + opp_bet),
            SeatState(seat=2, name="hero", stack=197.0, bet_street=hero_bet,
                      invested=3.0 + hero_bet, is_hero=True, hole_cards=hole),
        ],
    )


def _dist(strategy, snap):
    legal = legal_actions(snap)
    return strategy.distribution(snap, legal), {la.key for la in legal}


def test_micro_beats_fish_value_tuning():
    # каре: велью-ставим почти всегда и крупнее
    snap = _flop_snapshot(["7h", "7d"], ["7s", "7c", "Kd"])
    dist, keys = _dist(MicroExploitStrategy(rng=random.Random(3)), snap)
    assert set(dist) <= keys
    assert sum(v for k, v in dist.items() if k.startswith(("bet", "raise"))) > 0.8


def test_micro_barely_bluffs():
    # мусор без ставки: блеф почти выключен (на микролимитах не фолдят)
    snap = _flop_snapshot(["3h", "2d"], ["Ks", "Qc", "Jd"])
    dist, keys = _dist(MicroExploitStrategy(rng=random.Random(5)), snap)
    assert dist.get("check", 0.0) > 0.88


def test_base_heuristic_still_bluffs():
    # базовая эвристика на том же споте блефует заметно чаще
    snap = _flop_snapshot(["3h", "2d"], ["Ks", "Qc", "Jd"])
    dist, _ = _dist(HeuristicStrategy(rng=random.Random(5)), snap)
    assert sum(v for k, v in dist.items() if k.startswith("bet")) > 0.1


def test_fish_is_a_calling_station():
    fish = FishStrategy(rng=random.Random(1))
    # мусор против ставки всё равно иногда коллит
    snap = _flop_snapshot(["3h", "2d"], ["Ks", "Qc", "Jd"], hero_bet=0.0, opp_bet=6.0)
    dist, keys = _dist(fish, snap)
    assert set(dist) <= keys
    assert sum(v for k, v in dist.items() if k.startswith("call")) >= 0.3


def test_fish_bets_only_with_strength():
    fish = FishStrategy(rng=random.Random(2))
    # средняя рука без ставки — почти всегда чек
    snap = _flop_snapshot(["Ah", "7d"], ["As", "9c", "5d"])
    dist, _ = _dist(fish, snap)
    assert dist.get("check", 0.0) > 0.7
