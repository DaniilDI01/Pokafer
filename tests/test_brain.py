from pokafer.brain.api import Brain
from pokafer.brain.memory import InMemoryProfileStore
from pokafer.core.engine import HandEngine
from pokafer.analysis import stats_from_snapshots


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
