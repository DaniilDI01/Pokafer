from pokafer.core.engine import HandEngine


def _engine() -> HandEngine:
    return HandEngine(
        seat_stacks={1: 200.0, 2: 200.0},
        names={1: "sb", 2: "bb"},
        button_seat=1,  # HU: кнопка = SB, ходит первой на префлопе
        sb=1,
        bb=2,
        seed=7,
    )


def test_preflop_legal_for_sb():
    e = _engine()
    assert e.actor_seat == 1
    keys = [la.key for la in e.legal()]
    assert "fold" in keys
    assert "call:2" in keys          # уравнять до большого блайнда
    assert "raise:4" in keys         # мин-рейз до 2bb
    assert "raise:200" in keys       # олл-ин: 1 вложен + стек 199


def test_flop_first_to_act_is_bb():
    e = _engine()
    e.apply("raise", 6)
    assert e.actor_seat == 2
    e.apply("call")
    assert e.street == "flop"
    assert len(e.board) == 3
    assert e.actor_seat == 2  # постфлоп первым говорит не-баттон (BB)
    keys = [la.key for la in e.legal()]
    assert "check" in keys
    assert any(k.startswith("bet:") for k in keys)
    # меню сайзингов: ~полбанка/банк/олл-ин
    amts = [la.amount_to for la in e.legal() if la.action == "bet"]
    assert any(abs(a - 6.0) < 0.02 for a in amts)   # 0.5 * банк 12
    assert any(abs(a - 12.0) < 0.02 for a in amts)  # банк
    assert max(amts) == 194.0                       # олл-ин (200 - 6)


def test_hand_to_showdown_conserves_chips():
    e = _engine()
    e.apply("raise", 6)
    e.apply("call")
    for _ in range(3):        # flop/turn/river: чек-чек
        assert not e.hand_over
        e.apply("check")
        e.apply("check")
    assert e.hand_over
    assert e.result["type"] == "showdown"
    total = sum(s.stack for s in e.seats.values())
    assert abs(total - 400.0) < 1e-6
    assert abs(e.result["pot"] - 12.0) < 1e-6


def test_fold_ends_hand():
    e = _engine()
    e.apply("fold")
    assert e.hand_over
    assert e.result["type"] == "fold_win"
    assert e.result["winners"] == [2]
    assert abs(e.result["pot"] - 3.0) < 1e-6


def test_illegal_action_rejected():
    import pytest

    e = _engine()
    with pytest.raises(ValueError):
        e.apply("check")          # вне очереди ставок чекать нельзя — есть колл
    with pytest.raises(ValueError):
        e.apply("raise", 3)       # ниже мин-рейза
    with pytest.raises(ValueError):
        e.apply("raise", 999999)  # выше олл-ина


def test_uncalled_bet_refunded_hu_allin():
    # SB с большим стеком рейзит, BB закрывает олл-ином меньше — излишек вернётся
    e = HandEngine(
        seat_stacks={1: 200.0, 2: 30.0}, names={1: "a", 2: "b"},
        button_seat=1, seed=4,
    )
    e.apply("raise", 50)
    e.apply("call")  # BB: олл-ин 30 (все 30, стек меньше ставки)
    assert e.hand_over
    res = e.result
    assert res["type"] == "showdown"
    # вложились по 30: короткий стек может выиграть максимум 60, не 80
    assert res["pot"] == 60.0
    assert e.seats[2].stack <= 30.0 + 60.0
    assert abs(sum(s.stack for s in e.seats.values()) - 230.0) < 1e-6


def test_three_way_side_pots():
    # стеки 200/100/50, все вскрываются: главный банк 3x50, сайд 2x50
    e = HandEngine(
        seat_stacks={1: 200.0, 2: 100.0, 3: 50.0},
        names={1: "a", 2: "b", 3: "c"},
        button_seat=1, seed=8,
    )
    assert e.actor_seat == 1          # UTG (после BB на месте 3)
    e.apply("raise", 50)              # a: 50
    assert e.actor_seat == 2
    e.apply("raise", 100)             # b: 100
    assert e.actor_seat == 3
    e.apply("call")                   # c: олл-ин 50
    assert e.actor_seat == 1
    e.apply("call")                   # a: 100
    assert e.hand_over                # b и c олл-ин -> раздача до шоудауна
    res = e.result
    assert res["type"] == "showdown"
    pots = sorted(p["amount"] for p in res["pots"])
    assert pots == [100.0, 150.0]     # сайд 50*2 и главный 50*3, всего 250
    assert abs(sum(s.stack for s in e.seats.values()) - 350.0) < 1e-6
    # короткий стек выигрывает максимум главный банк: 50 + 150 = 200
    assert e.seats[3].stack <= 200.0 + 1e-6
