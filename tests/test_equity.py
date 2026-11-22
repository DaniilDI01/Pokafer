"""Тесты фундамента: быстрый оценщик, эквити, абстракция."""
import random

import pytest

from pokafer.core.abstraction import (
    HandClass,
    all_hand_classes,
    hand_bucket,
    hand_strength,
    infoset_key,
    load_ranking,
    nearest_bet_amount,
    preflop_class,
    preflop_class_id,
    stack_bucket,
)
from pokafer.core.cards import evaluate7, evaluate7_fast, parse_cards
from pokafer.core.equity import (
    equity_all_combos,
    equity_exact_river,
    equity_mc,
)


# ---------- evaluate7_fast ----------

def _random_hands(rng: random.Random, n: int, size: int) -> list[list[int]]:
    deck = list(range(52))
    out = []
    for _ in range(n):
        rng.shuffle(deck)
        out.append(deck[:size])
    return out


def test_fast_evaluator_matches_reference():
    rng = random.Random(11)
    for size in (5, 6, 7):
        for hand in _random_hands(rng, 3000, size):
            assert evaluate7_fast(hand) == evaluate7(hand)


def test_fast_evaluator_edge_cases():
    from pokafer.core.cards import parse_card

    def idx(cards):
        return [parse_card(c) for c in cards]

    assert evaluate7_fast(idx(["As", "Ks", "Qs", "Js", "Ts", "9s", "8s"])) == (8, 12)
    assert evaluate7_fast(idx(["Ah", "2c", "3d", "4s", "5h", "9d", "9c"])) == (4, 3)
    # кикер каре — карта из третьей пары бьёт одиночную двойку
    assert evaluate7_fast(idx(["Ah", "Ac", "Ad", "As", "Kd", "Kh", "2c"])) == (7, 12, 11)
    # две пары: кикер из третьей пары
    assert evaluate7_fast(idx(["Ah", "Ac", "Kd", "Ks", "Qd", "Qh", "2s"])) == (2, 12, 11, 10)


# ---------- эквити ----------

def test_equity_mc_matches_known_preflop_equities():
    rng = random.Random(3)
    for hole, ref in ((["As", "Ac"], 0.852), (["As", "Kd"], 0.654),
                      (["7d", "2c"], 0.347)):
        res = equity_mc(parse_cards(hole), [], n_rollouts=4000, rng=rng,
                        time_budget_ms=5000)
        assert abs(res.equity - ref) < 0.02, f"{hole}: {res.equity} vs {ref}"


def test_equity_river_exact_is_deterministic_and_matches_mc():
    rng = random.Random(5)
    hole = parse_cards(["As", "Kh"])
    board = parse_cards(["Qd", "7c", "2h", "9s", "5c"])
    combos = equity_all_combos(hole, board)
    r1 = equity_exact_river(hole, board, combos)
    r2 = equity_exact_river(hole, board, combos)
    assert r1.exact and r1.n == 990 and r1.equity == r2.equity
    mc = equity_mc(hole, board, n_rollouts=8000, rng=rng, time_budget_ms=5000)
    assert abs(mc.equity - r1.equity) < 0.02


def test_equity_drops_with_more_opponents():
    rng = random.Random(9)
    hole = parse_cards(["As", "Ac"])
    eqs = [
        equity_mc(hole, [], opponent_samplers=[None] * k, n_rollouts=3000,
                  rng=rng, time_budget_ms=5000).equity
        for k in (1, 3, 5)
    ]
    assert eqs[0] > eqs[1] > eqs[2] > 0.3


def test_equity_sampler_respects_dead_cards():
    rng = random.Random(2)
    hole = parse_cards(["As", "Ac"])
    dead = set(hole) | {parse_cards(["Kd", "Kc"])[0]}
    from pokafer.core.equity import _uniform_sampler

    for _ in range(200):
        c1, c2 = _uniform_sampler(rng, dead)
        assert c1 not in dead and c2 not in dead and c1 != c2


# ---------- абстракция ----------

def test_hand_classes_and_ids():
    classes = all_hand_classes()
    assert len(classes) == 169
    assert preflop_class(parse_cards(["As", "Ks"])) == HandClass(12, 11, True)
    assert preflop_class(parse_cards(["As", "Kd"])) == HandClass(12, 11, False)
    assert preflop_class(parse_cards(["7c", "7d"])) == HandClass(5, 5, False)
    # одинаковые ранги -> одинаковый id
    assert preflop_class_id(parse_cards(["Ah", "Ad"])) == \
        preflop_class_id(parse_cards(["Ac", "As"]))


def test_ranking_is_sorted_and_loaded():
    ranking = load_ranking()
    assert len(ranking.classes) == 169
    assert ranking.classes[0].label == "AA"
    eqs = ranking.equities
    assert eqs == sorted(eqs, reverse=True)
    assert abs(ranking.equities[0] - 0.85) < 0.05
    assert ranking.percentile(ranking.classes[0]) == 0.0
    assert ranking.percentile(ranking.classes[-1]) == 1.0


def test_hand_strength_monotonic_for_combinations():
    board = parse_cards(["9s", "5d", "2c"])
    high = hand_strength(parse_cards(["As", "Ac"]), board)
    weak = hand_strength(parse_cards(["7d", "2c"]), board)
    assert high > weak
    # одинаковые ранги: рука с флеши-дро сильнее бездро-аналога
    draw = hand_strength(parse_cards(["6s", "7s"]), parse_cards(["9s", "5s", "2c"]))
    nodraw = hand_strength(parse_cards(["6d", "7c"]), parse_cards(["9s", "5s", "2c"]))
    assert draw > nodraw


def test_hand_bucket_range_and_stacks():
    hole = parse_cards(["As", "Kd"])
    b = hand_bucket(hole, parse_cards(["9s", "5d", "2c"]), n_buckets=40)
    assert 0 <= b < 40
    assert stack_bucket(5) == 0 and stack_bucket(20) == 2 and stack_bucket(150) == 6


def test_abstract_bet_roundtrip():
    # доля банка 1.0 -> индекс 1 -> ближайшая сумма в легальных рамках
    idx = 1
    amt = nearest_bet_amount(idx, pot_after_call=20.0, max_bet=6.0,
                             min_to=12.0, max_to=194.0)
    assert 12.0 <= amt <= 194.0
    assert amt == pytest.approx(6.0 + 1.0 * 20.0)
    # олл-ин возвращает максимум
    assert nearest_bet_amount(3, 20.0, 6.0, 12.0, 194.0) == 194.0


def test_infoset_key_deterministic():
    k1 = infoset_key("flop", 12, 21, 3, ("c", "b1"))
    k2 = infoset_key("flop", 12, 21, 3, ("c", "b1"))
    k3 = infoset_key("flop", 12, 22, 3, ("c", "b1"))
    assert k1 == k2 and k1 != k3


def test_exact_river_tie_semantics_and_sum():
    """Борд с тремя двойками: тьи с любым тузом; win+tie+lose == 1, tie = доля банка."""
    hole = parse_cards(["Ah", "Qd"])
    board = parse_cards(["2s", "2h", "2d", "2c", "Kd"])
    res = equity_exact_river(hole, board, equity_all_combos(hole, board))
    assert res.exact and res.n == 990
    # тьи-комбо: у оппонента есть туз (3 живых туза: 3*42 + C(3,2) = 129)
    ties = 129
    assert res.win == pytest.approx((990 - ties) / 990)
    assert res.tie == pytest.approx(ties * 0.5 / 990)  # наша доля банка из сплитов
    # win+tie+lose < 1: остальное — доля банка соперникам в сплитах
    assert res.win + res.tie + res.lose == pytest.approx(1 - ties * 0.5 / 990)
    assert res.equity == pytest.approx(res.win + res.tie)


def test_equity_mc_empty_samplers_is_equity_one():
    hole = parse_cards(["As", "Ac"])
    res = equity_mc(hole, [], opponent_samplers=[], n_rollouts=100, rng=random.Random(1))
    assert res.equity == 1.0 and res.n == 0


def test_exact_river_empty_range_is_equity_one():
    hole = parse_cards(["As", "Ac"])
    board = parse_cards(["Kd", "7c", "2h", "9s", "5c"])
    res = equity_exact_river(hole, board, [])
    assert res.equity == 1.0 and res.n == 0 and res.exact


def test_equity_validates_inputs():
    hole = parse_cards(["As", "Ac"])
    with pytest.raises(ValueError):
        equity_mc(hole[:1], [])
    with pytest.raises(ValueError):
        equity_mc(hole, ["2c"] * 6)
    with pytest.raises(ValueError):
        equity_exact_river(hole, ["2c", "3c"], [])
