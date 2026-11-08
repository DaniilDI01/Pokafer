from pokafer.core.cards import RANK_CHARS, card_str, evaluate7, hand_name, parse_card


def test_parse_roundtrip():
    for i in range(52):
        assert parse_card(card_str(i)) == i


def test_parse_invalid():
    import pytest

    with pytest.raises(ValueError):
        parse_card("XX")
    with pytest.raises(ValueError):
        parse_card("AsKd")


def test_wheel_is_straight():
    cards = [parse_card(c) for c in ["Ah", "2c", "3d", "4s", "5h"]]
    score = evaluate7(cards)
    assert score[0] == 4  # стрит
    assert score[1] == 3  # пятёрка старшая


def test_category_order():
    royal = [parse_card(c) for c in ["Th", "Jh", "Qh", "Kh", "Ah"]]
    quads = [parse_card(c) for c in ["7c", "7d", "7h", "7s", "Kd"]]
    assert evaluate7(royal) > evaluate7(quads)
    assert evaluate7(royal)[0] == 8


def test_best_of_seven():
    # пара королей должна найтись среди 7 карт со старшей картой тузом
    cards = [parse_card(c) for c in ["Kh", "Kd", "Ah", "9c", "5s", "3d", "2h"]]
    score = evaluate7(cards)
    assert score[0] == 1
    assert score[1] == RANK_CHARS.index("K")
    assert "pair" in hand_name(score)
