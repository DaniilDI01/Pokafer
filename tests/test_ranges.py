"""Тесты модели диапазонов и equity-стратегии (легальность, статистика, позиции)."""
import random

import pytest

from pokafer.brain.api import Brain
from pokafer.brain.ranges import build_range, ranges_for_opponents
from pokafer.core.engine import HandEngine
from pokafer.core.legal import legal_actions
from pokafer.core.schema import SeatState, Snapshot


def _snapshot(hole, board, actor=2, opp_name="opp", hero_name="hero",
              opp_seat=1, actions=(), opp_stack=194.0, opp_bet=0.0,
              opp_invested=3.0, hero_invested=3.0) -> Snapshot:
    pot = 6.0 + hero_invested - 3.0 + opp_invested - 3.0 + opp_bet
    return Snapshot(
        hand_id="h1", sb=1, bb=2, button_seat=1, actor_seat=actor,
        street="flop" if board else "preflop", board=list(board), pot=pot,
        seats=[
            SeatState(seat=opp_seat, name=opp_name, stack=opp_stack,
                      bet_street=opp_bet, invested=opp_invested),
            SeatState(seat=3 - opp_seat, name=hero_name, stack=194.0,
                      bet_street=0, invested=hero_invested,
                      is_hero=True, hole_cards=list(hole)),
        ],
        actions=list(actions),
    )


# ---------- диапазоны ----------

def test_late_position_opens_wider_than_early():
    rng = random.Random(1)
    # HU: кнопка (первый в порядке) открывает шире, чем BB-опция? проверяем 6-max:
    snap6 = lambda seat, street="preflop": Snapshot(
        hand_id="h", sb=1, bb=2, button_seat=1, actor_seat=3 - 2, street=street,
        board=[], pot=3.0,
        seats=[SeatState(seat=i, name=f"p{i}", stack=200.0, bet_street=0,
                         invested=0.0) for i in range(1, 7)],
    )
    from pokafer.brain.ranges import W_BTN, W_UTG
    assert W_BTN > W_UTG
    # у лимпера диапазон уже, чем у баттон-опенера: строим два снапшота
    open_btn = Snapshot(
        hand_id="h", sb=1, bb=2, button_seat=6, actor_seat=6, street="preflop",
        board=[], pot=3.0,
        seats=[SeatState(seat=i, name=f"p{i}", stack=200.0, bet_street=0,
                         invested=0.0) for i in range(1, 7)],
        actions=[{"order": 0, "street": "preflop", "seat": 6, "type": "raise",
                  "amount_to": 6, "amount": 5}],
    )
    r = build_range(open_btn, 6)
    # баттон открыл: диапазон содержит сильные руки, ширина >= W_UTG
    assert len(r.classes) >= 16


def test_postflop_bet_narrows_range():
    snap_no_bet = _snapshot(["As", "Ac"], ["7s", "8d", "2h"])
    with_bet = Snapshot(
        hand_id="h1", sb=1, bb=2, button_seat=1, actor_seat=2, street="flop",
        board=["7s", "8d", "2h"], pot=18.0,
        seats=[
            SeatState(seat=1, name="opp", stack=182.0, bet_street=6.0,
                      invested=9.0),
            SeatState(seat=2, name="hero", stack=188.0, bet_street=0,
                      invested=9.0, is_hero=True, hole_cards=["As", "Ac"]),
        ],
        actions=[
            {"order": 0, "street": "preflop", "seat": 1, "type": "post",
             "amount_to": 1, "amount": 1},
            {"order": 1, "street": "preflop", "seat": 2, "type": "post",
             "amount_to": 2, "amount": 2},
            {"order": 2, "street": "preflop", "seat": 1, "type": "raise",
             "amount_to": 6, "amount": 5},
            {"order": 3, "street": "preflop", "seat": 2, "type": "call",
             "amount_to": 6, "amount": 4},
            {"order": 4, "street": "flop", "seat": 1, "type": "bet",
             "amount_to": 6, "amount": 6},
        ],
    )
    r1 = build_range(snap_no_bet, 1)
    r2 = build_range(with_bet, 1)
    assert len(r2.classes) < len(r1.classes)  # ставка сужает диапазон


def test_loose_profile_widens_range():
    snap = _snapshot(["As", "Ac"], ["7s", "8d", "2h"])
    base = len(build_range(snap, 1).classes)
    loose = len(build_range(
        snap, 1, {"vpip": 0.75, "pfr": 0.1, "af": 1.0, "fold_vs_bet": 0.2,
                  "hands": 50}).classes)
    tight = len(build_range(
        snap, 1, {"vpip": 0.12, "pfr": 0.08, "af": 3.0, "fold_vs_bet": 0.7,
                  "hands": 50}).classes)
    assert loose > base > tight


def test_folded_opponents_excluded():
    snap = _snapshot(["As", "Ac"], ["7s", "8d", "2h"])
    snap.seats[0].folded = True
    ranges = ranges_for_opponents(snap, 2)
    assert 1 not in ranges


def test_range_sampler_and_combos_respect_dead_cards():
    from pokafer.core.cards import parse_cards

    snap = _snapshot(["As", "Ac"], ["7s", "8d", "2h"])
    rng_range = build_range(snap, 1)
    rng = random.Random(4)
    dead = set(parse_cards(["As", "Ac", "7s", "8d", "2h"]))
    s = rng_range.sampler(rng)
    for _ in range(300):
        c1, c2 = s(rng, set(dead))
        assert c1 not in dead and c2 not in dead and c1 != c2
    for c1, c2 in rng_range.combos(set(dead)):
        assert c1 not in dead and c2 not in dead


# ---------- equity-стратегия ----------

def _play(seed: int, n_seats: int = 2, strategy="equity") -> HandEngine:
    e = HandEngine(
        seat_stacks={i: 200.0 for i in range(1, n_seats + 1)},
        names={i: f"p{i}" for i in range(1, n_seats + 1)},
        button_seat=1, sb=1, bb=2, seed=seed, hand_id=f"h{seed}",
    )
    brains = {i: Brain(strategy, seed=seed * 10 + i) for i in e.order}
    guard = 0
    while not e.hand_over:
        guard += 1
        assert guard < 600, "раздача зациклилась"
        seat = e.actor_seat
        d = brains[seat].decide(e.snapshot_for(seat))
        legal = {la.key for la in e.legal(seat)}
        assert d.key() in legal, f"{d.key()} вне легальных {legal}"
        e.apply(d.action, d.amount_to)
    total = sum(s.stack for s in e.seats.values())
    assert abs(total - 200.0 * n_seats) < 1e-6
    return e


def test_equity_plays_hu_and_nine_max_legally():
    for seed in range(5):
        _play(seed, n_seats=2)
    _play(99, n_seats=9)


def test_equity_is_deterministic_under_seed():
    e1 = _play(21)
    e2 = _play(21)
    assert e1.result["winners"] == e2.result["winners"]
    assert abs(e1.result["amount"] - e2.result["amount"]) < 1e-9


def test_equity_distribution_sums_to_one():
    snap = _snapshot(["As", "Ac"], ["7s", "8d", "2h"])
    legal = legal_actions(snap)
    for seed in range(10):
        brain = Brain("equity", seed=seed)
        dist = brain.strategy.distribution(snap, legal)
        assert abs(sum(dist.values()) - 1.0) < 1e-6
        assert set(dist) <= {la.key for la in legal}


def test_equity_folds_trash_facing_big_raise_and_raises_nuts():
    # слабая рука (только старшая карта) против ставки -> почти всегда фолд
    snap = _snapshot(["8d", "3c"], ["7s", "9d", "2h"], opp_bet=12.0,
                     opp_invested=15.0, hero_invested=3.0)
    snap.pot = 30.0
    legal = legal_actions(snap)
    brain = Brain("equity", seed=1)
    dist = brain.strategy.distribution(snap, legal)
    assert dist.get("fold", 0.0) > 0.5

    # пара тузов на ривере при своей игре -> почти всегда ставка
    snap2 = _snapshot(["As", "Ac"], ["7s", "8d", "2h", "9c", "Jd"])
    snap2.street = "river"
    legal2 = legal_actions(snap2)
    dist2 = Brain("equity", seed=1).strategy.distribution(snap2, legal2)
    bet_prob = sum(v for k, v in dist2.items() if k.startswith(("bet", "raise")))
    assert bet_prob > 0.6


def test_equity_uses_profiles_when_given():
    # станция (низкий fold_vs_bet): блефов почти нет; фолдер: блеф включается
    snap = _snapshot(["8d", "3c"], ["7s", "9d", "2h"])  # слабая рука, своя игра
    legal = legal_actions(snap)
    station = {"opp": {"vpip": 0.8, "pfr": 0.1, "af": 0.5, "fold_vs_bet": 0.1,
                       "hands": 50}}
    folder = {"opp": {"vpip": 0.2, "pfr": 0.1, "af": 2.0, "fold_vs_bet": 0.8,
                      "hands": 50}}
    d1 = Brain("equity", seed=1).strategy.distribution(snap, legal, station)
    d2 = Brain("equity", seed=1).strategy.distribution(snap, legal, folder)
    bluff1 = sum(v for k, v in d1.items() if k.startswith(("bet", "raise")))
    bluff2 = sum(v for k, v in d2.items() if k.startswith(("bet", "raise")))
    assert bluff2 > bluff1


def test_brain_decide_accepts_explicit_profiles():
    snap = _snapshot(["As", "Ac"], ["7s", "8d", "2h"])
    legal = legal_actions(snap)
    e = HandEngine(seat_stacks={1: 200.0, 2: 200.0}, names={1: "opp", 2: "hero"},
                   button_seat=1, seed=1)
    brain = Brain("equity", seed=1, memory=None)
    d = brain.decide(snap_for(snap), profiles={"opp": {"vpip": 0.3, "hands": 20}})
    assert d.key() in {la.key for la in legal}
    assert "VPIP 30%" in (d.reasoning or "")


def snap_for(snap: Snapshot) -> Snapshot:
    return snap
