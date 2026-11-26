import pytest

from pokafer.analysis import stats_from_snapshots
from pokafer.brain.api import Brain
from pokafer.brain.memory import InMemoryProfileStore
from pokafer.brain.strategies.base import Strategy
from pokafer.core.engine import HandEngine


def test_brain_plays_legal_hand_to_end():
    e = HandEngine(
        seat_stacks={1: 200.0, 2: 200.0},
        names={1: "a", 2: "b"},
        button_seat=1, seed=11,
    )
    brain = Brain("heuristic", seed=42)
    guard = 0
    while not e.hand_over:
        guard += 1
        assert guard < 100, "раздача зациклилась"
        snap = e.snapshot_for(e.actor_seat)
        d = brain.decide(snap)
        assert d.key() in {la.key for la in e.legal()}, f"{d.key()} вне легальных"
        e.apply(d.action, d.amount_to)
    assert e.result["type"] in ("showdown", "fold_win")


def test_decide_json_interface():
    e = HandEngine(seat_stacks={1: 100.0, 2: 100.0}, names={1: "a", 2: "b"}, button_seat=1, seed=3)
    brain = Brain("random", seed=1)
    out = brain.decide_json(e.snapshot_for(e.actor_seat).model_dump())
    assert set(out) >= {"action", "amount_to", "distribution", "decision", "elapsed_ms"}
    legal = {la.key for la in e.legal()}
    assert out["decision"] in legal


def test_memory_collects_opponent_stats():
    mem = InMemoryProfileStore()
    brain = Brain("heuristic", memory=mem, seed=9)
    e = HandEngine(
        seat_stacks={1: 200.0, 2: 200.0},
        names={1: "Вы", 2: "Мозг"},
        button_seat=1, seed=21,
    )
    guard = 0
    while not e.hand_over:
        guard += 1
        assert guard < 100
        snap = e.snapshot_for(e.actor_seat)
        brain.observe(snap)
        d = brain.decide(snap)
        e.apply(d.action, d.amount_to)
    profiles = mem.profiles()
    assert set(profiles) == {"Вы", "Мозг"}
    assert profiles["Вы"]["hands"] == 1
    stats = stats_from_snapshots([e.snapshot_for(1)])
    assert stats["Вы"]["hands"] == 1


PASSIVE = ("fold", "check", "call")


def _small_blind_table() -> HandEngine:
    """HU, малый блайнд (место 1) ходит первым: можно фолдить, коллить и рейзить."""
    return HandEngine(seat_stacks={1: 100.0, 2: 100.0}, names={1: "a", 2: "b"}, button_seat=1, seed=3)


@pytest.mark.parametrize("strategy", ["random", "heuristic", "equity"])
def test_decide_can_be_limited_to_passive_actions(strategy):
    """Платформа ограничила меню (история раздачи неточна): решения — только разрешённые, вес остальных отброшен."""
    table = _small_blind_table()
    snap, legal = table.snapshot_for(1), {la.key for la in table.legal()}
    assert any(key.startswith("raise") for key in legal)
    brain = Brain(strategy, seed=1)
    for _ in range(20):
        d = brain.decide(snap, allowed=PASSIVE)
        assert d.action in PASSIVE and d.key() in legal
        assert {key.partition(":")[0] for key in d.distribution} <= set(PASSIVE)
        assert sum(d.distribution.values()) == pytest.approx(1.0)


def test_decide_falls_back_to_call_when_nothing_allowed_remains():
    """Стратегия хотела только рейзить, а рейз запрещён: остаётся колл — не первый элемент меню (фолд)."""
    class OnlyRaises(Strategy):
        name = "only_raises"

        def distribution(self, snap, legal, profiles=None):
            return {la.key: 1.0 for la in legal if la.action in ("bet", "raise")}

    snap = _small_blind_table().snapshot_for(1)
    brain = Brain("random", seed=1)
    brain.strategy = OnlyRaises()
    assert brain.decide(snap).action == "raise"
    d = brain.decide(snap, allowed=PASSIVE)
    assert d.action == "call" and d.distribution == {"call:2": 1.0}
