from pokafer.core.schema import Decision, Snapshot


def _minimal_snapshot() -> Snapshot:
    return Snapshot(
        hand_id="h0001",
        table_id="t1",
        sb=1,
        bb=2,
        button_seat=1,
        actor_seat=2,
        street="preflop",
        pot=3,
        seats=[
            {
                "seat": 1, "name": "p1", "stack": 199.0, "bet_street": 1.0,
                "invested": 1.0, "is_hero": False,
            },
            {
                "seat": 2, "name": "hero", "stack": 198.0, "bet_street": 2.0,
                "invested": 2.0, "is_hero": True, "hole_cards": ["As", "Kh"],
            },
        ],
        actions=[{"order": 0, "street": "preflop", "seat": 1, "type": "post", "amount_to": 1, "amount": 1}],
    )


def test_json_roundtrip():
    snap = _minimal_snapshot()
    data = snap.model_dump()
    restored = Snapshot.model_validate(data)
    assert restored == snap
    assert restored.seats[1].hole_cards == ["As", "Kh"]


def test_decision_key():
    assert Decision(action="raise", amount_to=6.0).key() == "raise:6"
    assert Decision(action="fold").key() == "fold"
