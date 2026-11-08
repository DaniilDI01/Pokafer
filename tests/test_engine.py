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
