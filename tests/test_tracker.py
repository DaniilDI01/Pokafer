"""Трекер без браузера: движок стола играет, глаза «видят» кадры, трекер восстанавливает раздачу.

Кадры здесь не пиксели, а ровно то, что глаза выдали бы с этих пикселей
(Perception собирается из вида стола). Часть состояний между кадрами
пропадает — как в живой игре: трекер обязан восстановить раздачу по итогам.
"""
import json
import random
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from pokafer.eyes import sites
from pokafer.eyes.cards import Reading
from pokafer.eyes.flags import FlagReading
from pokafer.eyes.history import Goal, Round, plan, shape
from pokafer.eyes.level import Table, level_of
from pokafer.eyes.numbers import NumberReading
from pokafer.eyes.perceive import CardSeen, Perception, SeatCards
from pokafer.eyes.sites import load_preset
from pokafer.eyes.tracker import HERO, Tracker
from pokafer.eyes.truth import Judge, compare_hand, history_verdict, records

_act = lambda la: (la.action, la.amount_to)
from pokafer.web.table_server import HERO as WEB_HERO, TableSession

LAYOUTS = [["pos_top"], ["pos_tl", "pos_tr"], ["pos_left", "pos_top", "pos_right"],
           ["pos_left", "pos_tl", "pos_tr", "pos_right"],
           ["pos_left", "pos_tl", "pos_top", "pos_tr", "pos_right"]]
ORDER = ["hero", "pos_left", "pos_tl", "pos_top", "pos_tr", "pos_right"]   # по часовой от героя
BLANK = Image.new("RGB", (1, 1))


def _rect(i: int) -> str:
    return f"[{0.05 + 0.06 * i:.3f}, 0.100, 0.050, 0.100]"


@pytest.fixture
def preset(tmp_path, monkeypatch):
    """Пресет «t»: раскладка и порядок мест — как у нашего стола; индикаторы хода, дилера и итога."""
    monkeypatch.setattr(sites, "EYES_DIR", tmp_path)
    (tmp_path / "sites.toml").write_text(
        '[presets.t]\ncalibration = "t.toml"\n\n[domains]\n"localhost" = "t"\n', encoding="utf-8")
    seats = "".join(f"[seat.{pos}]\ncards = [{_rect(3)}, {_rect(4)}]\nbacks = [{_rect(5)}, {_rect(6)}]\n"
                    for pos in ORDER[1:])
    marks = "".join(f'"seat.{pos}.button" = {_rect(9)}\n"seat.{pos}.turn" = {_rect(10)}\n'
                    for pos in ORDER[1:])
    (tmp_path / "t.toml").write_text(
        "[meta]\nviewport_ref = [1280, 720]\ncoords = \"ref_px\"\ncalibration_id = \"id1\"\n"
        f"seat_layouts = {json.dumps(LAYOUTS)}\n"
        f"[hero]\ncards = [{_rect(0)}, {_rect(1)}]\n[board]\nslots = [{', '.join(_rect(i) for i in range(5))}]\n"
        f"[table]\nseat_order = {json.dumps(ORDER)}\nsb = 1.0\nbb = 2.0\n"
        f"[flags]\n\"hero.turn\" = {_rect(7)}\n\"hero.button\" = {_rect(8)}\n\"hand.over\" = {_rect(11)}\n{marks}"
        + seats, encoding="utf-8")
    return load_preset("t")


def _slot(path: str, i: int, kind: str):
    return SimpleNamespace(path=f"{path}[{i}]", rect=(0, 0, 1, 1), kind=kind)


def _num(value) -> NumberReading:
    return NumberReading("number", float(value), "", 0.99)


def _seated(view: dict) -> list[str]:
    return LAYOUTS[len(view["bots"]) - 1]


def frame_of(view: dict, seq: int = 0) -> Perception:
    """Кадр, каким его выдали бы глаза на этом состоянии стола."""
    hero, over, layout = view["hero"], view["hand_over"], _seated(view)
    cards = [CardSeen(_slot("hero.cards", i, "hero"),
                      Reading("face", c) if c and not hero["folded"] else Reading("unknown"))
             for i, c in enumerate(hero["cards"])]
    cards += [CardSeen(_slot("board.slots", i, "board"),
                       Reading("face", view["board"][i]) if i < len(view["board"]) else Reading("empty"))
              for i in range(5)]
    numbers = {"hero.stack": _num(hero["stack"]), "pot.amount": _num(view["pot"]),
               "hero.bet": _num(hero["bet"]) if hero["bet"] else NumberReading("empty")}
    if over or view["to_act"] != WEB_HERO:
        numbers["act.call_amount"] = NumberReading("empty")            # кнопки погашены
    elif view["legal"]["call"] is None:
        numbers["act.call_amount"] = NumberReading("none", text="Чек")
    else:
        numbers["act.call_amount"] = _num(view["legal"]["call"])
    seats: dict[str, SeatCards] = {}
    flags = {"hero.turn": FlagReading("on" if view["to_act"] == WEB_HERO else "off"),
             "hero.button": FlagReading("on" if view["button_seat"] == WEB_HERO else "off"),
             "hand.over": FlagReading("on" if over else "off")}
    for pos in ORDER[1:]:
        numbers.setdefault(f"seat.{pos}.stack", NumberReading("empty"))   # место пусто
    for bot, pos in zip(view["bots"], layout):
        numbers[f"seat.{pos}.stack"] = _num(bot["stack"])
        numbers[f"seat.{pos}.bet"] = _num(bot["bet"]) if bot["bet"] else NumberReading("empty")
        shown = isinstance(bot["cards"], list)
        seats[pos] = SeatCards("revealed" if shown else "none" if bot["folded"] else "hidden",
                               list(bot["cards"]) if shown else [])
        flags[f"seat.{pos}.turn"] = FlagReading("on" if bot["seat"] == view["to_act"] else "off")
        flags[f"seat.{pos}.button"] = FlagReading("on" if bot["seat"] == view["button_seat"] else "off")
    return Perception(seq, BLANK, cards, numbers, {}, seats, {}, flags, {})


_records = records                                  # и записи трекера, и движка — одним видом


def assert_level(tracker: Tracker, session: TableSession, invested: bool = True, strict: bool = True,
                hero_cards: bool = True) -> None:
    """Подтверждённый срез совпадает с движком: уровень обязан быть точным всегда.

    strict = False (состояния пропадали между кадрами): «был ли олл-ин» у места,
    выигравшего после пропущенного олл-ина, из кадров не узнать.
    """
    snap, truth, view = tracker.snapshot, session.engine.snapshot_for(WEB_HERO), session.view()
    assert snap is not None and tracker.phase == ("over" if view["hand_over"] else "in_hand")
    assert snap.board == truth.board and snap.street == truth.street
    assert snap.button_seat == truth.button_seat and snap.actor_seat == HERO
    assert snap.pot == pytest.approx(view["pot"]) and snap.pot == pytest.approx(tracker.level.pot)
    assert snap.hand_id == f"h{tracker.hand.no:04d}" and snap.table_id == "t"

    def shape(s, keep_invested):
        return (s.seat, round(s.stack, 2), round(s.bet_street, 2), round(s.invested, 2) if keep_invested else 0,
                s.folded, s.all_in and (strict or s.stack <= 0), s.is_hero)

    assert [shape(s, invested) for s in snap.seats] == [shape(s, invested) for s in truth.seats]
    assert [s.name for s in snap.seats] == ["hero", *_seated(view)]       # места — по часовой от героя
    if hero_cards:                                                        # карты героя трекер помнит
        assert snap.seats[0].hole_cards == truth.seats[0].hole_cards
    shown = {b["seat"]: b["cards"] for b in view["bots"] if isinstance(b["cards"], list)}
    known = {s.seat: s.hole_cards for s in snap.seats if s.hole_cards}          # вскрытые карты — тоже
    assert known == ({WEB_HERO: truth.seats[0].hole_cards, **shown} if hero_cards else shown)
    assert tracker.hero_turn == (view["to_act"] == WEB_HERO and not view["hand_over"])


def _session(bots: int) -> TableSession:
    """Стол с воспроизводимой колодой: зерно сессии задаёт карты каждой руки."""
    return TableSession(seed=1000 + bots,
                        bots=[{"name": f"Мозг {i}", "strategy": "heuristic"} for i in range(1, bots + 1)])


def _choose(engine, rng: random.Random):
    """Случайный легальный ход: треть — рейз, десятая часть — фолд, остальное — чек/колл."""
    legal = engine.legal(engine.actor_seat)
    raises = [la for la in legal if la.action in ("bet", "raise")]
    roll = rng.random()
    if raises and roll < 0.30:
        return rng.choice(raises)
    if roll < 0.40:
        folds = [la for la in legal if la.action == "fold"]
        if folds:
            return folds[0]
    return next(la for la in legal if la.action in ("check", "call"))


def play(tracker: Tracker, session: TableSession, hands: int, rng: random.Random,
         drop: float = 0.0, repeat=(2, 2), tamper=None, strict=None, choose=None) -> dict:
    """Сыграть hands раздач движком, показывая трекеру состояния по ходу (drop — доля пропущенных).

    Состояние, показанное дважды подряд, трекер обязан подтвердить — срез
    сверяется с движком. tamper(show) подсовывает кадр между двумя хорошими.
    Возврат: раздачи и то, как восстановлена каждая — действие в действие,
    иным, но равным по вложениям объяснением (consistent), с пропуском
    (gapped) либо с выдуманными деньгами (diffs — так бывает только при ошибке).
    """
    out = {"hands": 0, "equal": 0, "equivalent": 0, "consistent": 0, "gapped": 0,
           "events": Counter(), "diffs": []}

    def show(view: dict | None = None) -> list[str]:
        events = tracker.feed(frame_of(view if view is not None else session.view()))
        out["events"].update(e.kind for e in events)
        return [e.kind for e in events]

    joined = tracker.phase == "in_hand"                # рука уже шла, когда трекер её увидел
    strict = drop == 0 if strict is None else strict
    for _ in range(hands):
        engine = session.engine
        show()
        show()
        assert_level(tracker, session, invested=tracker.hand.start is not None, strict=strict)
        while not engine.hand_over:
            action = (choose or _choose)(engine, rng)
            engine.apply(action.action, action.amount_to)
            if rng.random() < drop:
                continue                               # состояние прошло между кадрами
            times = rng.randint(*repeat)
            show()
            if tamper is not None:
                tamper(show)
                show()
            for _ in range(times - 1):
                show()
            if times >= 2:
                assert_level(tracker, session, invested=tracker.hand.start is not None,
                              strict=strict)
        show()
        show()
        assert_level(tracker, session, invested=tracker.hand.start is not None, strict=strict)
        out["hands"] += 1
        mine, truth = _records(tracker.snapshot.actions), _records(engine.actions)
        if joined:                                     # история этой руки началась не с раздачи карт
            joined = False
        else:
            verdict = history_verdict(mine, truth, tracker.hand.gaps)
            out[{"точно": "equal", "эквивалент": "equivalent", "иной рассказ": "consistent",
                 "пропуск": "gapped"}.get(verdict)] += 1
            if verdict == "выдумано":
                out["diffs"].append((mine, truth))
        session.settle_hand()
        session._start_hand()
    return out


# ---------- каждое действие видно отдельно: история — ровно как у движка ----------

@pytest.mark.parametrize("bots", [1, 2, 3, 5])
def test_every_action_seen_history_is_exact(preset, bots):
    tracker = Tracker(preset)
    out = play(tracker, _session(bots), hands=14, rng=random.Random(100 + bots))
    assert out["hands"] == 14 and out["equal"] + out["equivalent"] == 14 and out["diffs"] == []
    assert out["consistent"] == out["gapped"] == 0
    assert out["events"]["gap"] == out["events"]["resync"] == 0
    assert out["events"]["hand_start"] == 14 and out["events"]["hand_over"] == 14
    assert out["events"]["actions"] > 0 and tracker.hand.gaps == 0


# ---------- состояния пропадают между кадрами: раздача восстанавливается по итогам ----------

@pytest.mark.parametrize("bots", [2, 4])
def test_states_missed_between_frames_never_invent_money(preset, bots):
    """Уровень точен всегда; история — либо верна, либо честно помечена пропуском."""
    tracker = Tracker(preset)
    out = play(tracker, _session(bots), hands=30, rng=random.Random(7 + bots), drop=0.45, repeat=(1, 2))
    assert out["hands"] == 30 and out["diffs"] == []
    assert out["equal"] > 0
    assert out["equal"] + out["equivalent"] + out["consistent"] + out["gapped"] == 30
    assert tracker.stats["интервал не восстановлен"] == out["events"]["gap"]


def _flop_with_bots_first(preset, tracker: Tracker, hero_folds: bool = False):
    """Стол на три места, кнопка у героя: постфлоп первыми ходят оба бота. Все кадры — по два.

    hero_folds: герой сбрасывает на префлопе — боты остаются вдвоём, и тогда
    «чек — ставка — колл» первого бота неотличим от «ставка — колл».
    """
    session = _session(2)
    session.button = 3
    session._start_hand()
    engine = session.engine

    def feed(view: dict | None = None) -> list[str]:
        return [e.kind for e in tracker.feed(frame_of(view if view is not None else session.view()))]

    while engine.street == "preflop":
        feed()
        feed()
        if hero_folds and engine.actor_seat == WEB_HERO:
            engine.apply("fold")
        else:
            engine.apply("check" if any(la.action == "check" for la in engine.legal()) else "call")
    feed()
    feed()
    assert engine.street == "flop" and engine.actor_seat == 2 and tracker.exact
    return session, feed


def _last(tracker: Tracker, n: int = 2) -> list[tuple]:
    return [(a.street, a.seat, a.type, a.amount_to) for a in tracker.snapshot.actions][-n:]


def test_single_frame_witness_shows_who_bet(preset):
    """Боты вдвоём: «ставка — колл» и «чек — ставка — колл» дают один итог. Их различает один кадр."""
    tracker = Tracker(preset)
    session, feed = _flop_with_bots_first(preset, tracker, hero_folds=True)
    session.engine.apply("bet", 2.0)
    assert feed() == []                               # ставка увидена один раз: ничего не подтверждает
    session.engine.apply("call")
    assert feed() + feed() == ["actions", "street"]   # колл — дважды: подтверждено, круг закрыт — терн
    assert tracker.exact and _last(tracker) == [("flop", 2, "bet", 2.0), ("flop", 3, "call", 2.0)]
    assert tracker.snapshot.seats[0].folded and not tracker.hero_turn
    assert_level(tracker, session)

    tracker = Tracker(preset)                         # тот же розыгрыш, но кадра со ставкой не было
    session, feed = _flop_with_bots_first(preset, tracker, hero_folds=True)
    session.engine.apply("bet", 2.0)
    session.engine.apply("call")
    assert feed() + feed() == ["gap", "street"]       # кто ставил — не видно: трекер не угадывает
    assert not tracker.exact and not [a for a in tracker.snapshot.actions if a.street == "flop"]
    assert_level(tracker, session)


def test_contradicting_witness_is_discarded(preset):
    """Свидетель, через которого не проходит ни одна история, — сбой: история ищется без него."""
    tracker = Tracker(preset)
    session, feed = _flop_with_bots_first(preset, tracker)
    ghost = json.loads(json.dumps(session.view()))    # «ставка героя», которой не было
    ghost["hero"]["bet"], ghost["pot"] = 5.0, 8.0
    feed(ghost)                                       # один раз
    session.engine.apply("bet", 2.0)                  # на деле ставит место 2
    events = feed() + feed()
    assert events == ["actions"] and tracker.exact
    assert _last(tracker, 1) == [("flop", 2, "bet", 2.0)]
    assert tracker.stats["кадр-свидетель отброшен"] == 1
    assert_level(tracker, session)


def test_glitch_frame_between_good_ones_changes_nothing(preset):
    """Один кадр — не факт: испорченный кадр между двумя хорошими ничего не меняет."""
    tracker, session = Tracker(preset), _session(3)
    rng = random.Random(3)
    fed = []

    def tamper(show):
        wrong = json.loads(json.dumps(session.view()))
        wrong["pot"] += 1.0                           # «не тот» банк на один кадр
        fed.extend(show(wrong))

    out = play(tracker, session, hands=6, rng=rng, tamper=tamper)
    assert fed == [] and out["equal"] + out["equivalent"] == 6 and out["diffs"] == []
    assert tracker.hand.gaps == 0 and out["events"]["gap"] == 0


def test_unreliable_frame_is_not_information(preset):
    """Неуверенное число и «не распознано» не подтверждают состояние и не сбивают ожидаемое."""
    tracker, session = Tracker(preset), _session(2)
    view = session.view()
    assert tracker.feed(frame_of(view)) == []          # увидено один раз — ждём второго
    unsure = frame_of(view)
    numbers = dict(unsure.numbers, **{"hero.stack": NumberReading("low", 197.0, "197", 0.3)})
    tracker.feed(Perception(unsure.seq, BLANK, unsure.cards, numbers, {}, unsure.seats, {}, unsure.flags, {}))
    assert tracker.stats["кадр недостоверный: hero.stack"] == 1
    bets = dict(unsure.numbers, **{"seat.pos_tl.bet": NumberReading("low", 6.0, "6", 0.4)})
    tracker.feed(Perception(unsure.seq, BLANK, unsure.cards, bets, {}, unsure.seats, {}, unsure.flags, {}))
    assert tracker.stats["кадр недостоверный: seat.pos_tl.bet"] == 1     # не «фишка без ставки»
    board = [CardSeen(c.slot, Reading("unknown") if c.slot.path == "board.slots[0]" else c.reading)
             for c in unsure.cards]
    tracker.feed(Perception(unsure.seq, BLANK, board, unsure.numbers, {}, unsure.seats, {}, unsure.flags, {}))
    assert tracker.stats["кадр недостоверный: board.slots[0]"] == 1
    kinds = [e.kind for e in tracker.feed(frame_of(view))]
    assert tracker.phase == "in_hand" and tracker.hand.hero_cards == tuple(view["hero"]["cards"])
    assert kinds[0] == "hand_start"


def test_wrong_pair_of_frames_is_caught_by_the_money(preset):
    """Два одинаковых неверных кадра подтверждаются — но банк выдаёт обман (resync)."""
    tracker, session = Tracker(preset), _session(3)
    for _ in range(2):
        tracker.feed(frame_of(session.view()))
    assert tracker.exact and tracker.hand.start is not None
    wrong = json.loads(json.dumps(session.view()))
    wrong["pot"] = 9.0                                   # банк больше, чем ушло со стеков
    events = []
    for _ in range(2):
        events += tracker.feed(frame_of(wrong))
    assert [e.kind for e in events] == ["resync"]
    assert not tracker.exact and tracker.hand.start is None
    assert tracker.stats["банк не сходится с деньгами на столе"] == 1


def test_joined_mid_hand_is_honest_about_its_history(preset):
    """Стол встречен на флопе: уровень точен, история неполна — и трекер об этом говорит."""
    tracker, session = Tracker(preset), _session(3)
    rng = random.Random(5)
    while len(session.view()["board"]) < 3:             # доигрываем префлоп без трекера
        action = _choose(session.engine, rng)
        session.engine.apply(action.action, action.amount_to)
    events = []
    for _ in range(2):
        events += tracker.feed(frame_of(session.view()))
    assert [e.kind for e in events][0] == "hand_start" and "gap" not in [e.kind for e in events]
    assert tracker.phase == "in_hand" and not tracker.exact and tracker.snapshot.actions == []
    assert_level(tracker, session, invested=False)
    out = play(tracker, session, hands=2, rng=rng)
    assert out["equal"] + out["equivalent"] == 1 and out["diffs"] == []


def test_whole_hand_passing_between_frames_is_a_new_hand(preset):
    """Рука началась и окончилась, не подтвердившись ни разу: её итог — не «дорисовка» прошлой."""
    tracker, session = Tracker(preset), _session(3)
    rng = random.Random(29)
    play(tracker, session, hands=2, rng=rng)           # вторая рука окончена и подтверждена
    finished, before = tracker.hand, tracker.snapshot.model_dump()
    engine = session.engine                            # третья рука идёт без трекера, до конца
    while not engine.hand_over:
        action = _choose(engine, rng)
        engine.apply(action.action, action.amount_to)
    events = []
    for _ in range(2):
        events += tracker.feed(frame_of(session.view()))
    assert [e.kind for e in events] == ["hand_start", "hand_over"]
    assert tracker.hand is not finished and tracker.hand.no == finished.no + 1
    assert tracker.snapshot.seats[0].hole_cards == engine.snapshot_for(WEB_HERO).seats[0].hole_cards
    assert not tracker.exact and tracker.snapshot.actions == []     # историю выдумывать не стал
    assert tracker.stats["рука прошла между кадрами"] == 1
    assert_level(tracker, session, invested=False, strict=False)   # чей был олл-ин — не видно


def test_missed_hand_with_the_same_board_size_is_a_new_hand(preset):
    """Герой сбросил в двух руках подряд, обе дошли до вскрытия: борд той же длины, карты героя не читаются."""
    passive = lambda engine, rng: next(la for la in engine.legal() if la.action in ("check", "call"))
    tracker, session = Tracker(preset), _session(3)
    play(tracker, session, hands=1, rng=random.Random(4), choose=passive)   # вскрытие: пять карт
    finished = tracker.hand
    assert len(finished.board) == 5 and tracker.phase == "over"
    while not session.engine.hand_over:              # следующая рука идёт без трекера
        engine = session.engine
        folds = [la for la in engine.legal() if la.action == "fold"]
        action = folds[0] if folds and engine.actor_seat == WEB_HERO else passive(engine, None)
        engine.apply(action.action, action.amount_to)
    events = []
    for _ in range(2):
        events += tracker.feed(frame_of(session.view()))
    assert [e.kind for e in events] == ["hand_start", "hand_over"]
    assert tracker.hand is not finished and len(tracker.hand.board) == 5
    assert tracker.snapshot.board == session.view()["board"] and tracker.snapshot.seats[0].folded
    assert tracker.stats["рука прошла между кадрами"] == 1 and not tracker.exact and not tracker.from_start
    assert tracker.snapshot.seats[0].hole_cards is None   # карты сбросившего героя не читаются — честно «неизвестны»
    assert_level(tracker, session, invested=False, strict=False, hero_cards=False)


def test_hero_fold_is_seen_by_his_dimmed_cards(preset):
    tracker, session = Tracker(preset), _session(2)
    rng = random.Random(11)
    engine = session.engine
    for _ in range(2):
        tracker.feed(frame_of(session.view()))
    while engine.actor_seat != WEB_HERO or not any(la.action == "fold" for la in engine.legal(WEB_HERO)):
        action = _choose(engine, rng)
        engine.apply(action.action, action.amount_to)
        for _ in range(2):
            tracker.feed(frame_of(session.view()))
    engine.apply("fold")
    events = []
    for _ in range(2):
        events += tracker.feed(frame_of(session.view()))
    assert tracker.snapshot.seats[0].folded and tracker.snapshot.seats[0].hole_cards
    assert not tracker.hero_turn and "hero_turn" not in [e.kind for e in events]
    assert any(a.type == "fold" and a.seat == HERO for a in tracker.snapshot.actions)
    assert_level(tracker, session)


def test_headsup_blinds_and_who_acts_first(preset):
    """HU: кнопка ставит малый блайнд и ходит первой на префлопе."""
    tracker, session = Tracker(preset), _session(1)
    for _ in range(2):
        tracker.feed(frame_of(session.view()))
    small = tracker.snapshot.button_seat
    assert [(a.seat, a.type, a.amount_to) for a in tracker.snapshot.actions] == [
        (small, "post", 1.0), (3 - small, "post", 2.0)]
    assert_level(tracker, session)


def test_next_hands_rotate_the_button_and_carry_stacks(preset):
    tracker = Tracker(preset)
    out = play(tracker, _session(3), hands=4, rng=random.Random(17))
    assert out["equal"] + out["equivalent"] == 4 and tracker.hand.no == 4


# ---------- история: поиск продолжения круга ----------

STACKS = {1: 100.0, 2: 100.0, 3: 100.0, 4: 100.0}


def _round(bets: dict, stacks: dict, actor=None, button=3, anchor=None, acted=False, street=1, folded=(),
           invested=None):
    ring = tuple(sorted(bets))
    return Round(ring, button, 1.0, 2.0, street, 10.0, dict(bets), dict(stacks), frozenset(folded),
                 {s: 0.0 for s in ring} if invested is None else invested,
                 button if anchor is None else anchor, acted, actor, [])


def _goal(street: int, bets: dict, put: dict | None, folded=(), actor=None, known=True, over=False,
          pot_delta=0.0, via=()) -> Goal:
    return Goal(street, dict(bets), None if put is None else dict(put), frozenset(folded), actor, known, over,
                pot_delta, tuple(via))


def _steps(records) -> list[tuple]:
    return [(a.seat, a.type, a.amount_to, a.amount) for a in records]


def test_bet_then_calls_needs_the_bet_to_have_been_seen():
    """По итогам «ставка и два колла» не отличить от «чек — ставка — два колла»: кто ставил?"""
    start = _round({1: 0, 2: 0, 3: 0}, STACKS, actor=1, button=3)
    goal = dict(folded=(), actor=1, pot_delta=30)
    assert plan(start, _goal(2, {1: 0, 2: 0, 3: 0}, {1: 10, 2: 10, 3: 10}, **goal)) is None
    seen = shape(1, {1: 10, 2: 0, 3: 0}, (), 2)          # один кадр: ставка лежит, ход у второго
    assert _steps(plan(start, _goal(2, {1: 0, 2: 0, 3: 0}, {1: 10, 2: 10, 3: 10}, via=[seen], **goal))) == [
        (1, "bet", 10.0, 10.0), (2, "call", 10.0, 10.0), (3, "call", 10.0, 10.0)]


def test_plan_sees_checks_by_whose_turn_it_became():
    start = _round({1: 0, 2: 0, 3: 0}, STACKS, actor=1, button=3)      # кнопка (3) ходит последней
    assert _steps(plan(start, _goal(1, {1: 0, 2: 0, 3: 0}, {1: 0, 2: 0, 3: 0}, actor=3))) == [
        (1, "check", None, 0.0), (2, "check", None, 0.0)]


def test_plan_orders_folds_and_calls_around_a_seen_bet():
    start = _round({1: 0, 2: 0, 3: 0}, STACKS, actor=1, button=3)
    seen = shape(1, {1: 10, 2: 0, 3: 0}, (), 2)
    goal = dict(folded={2}, actor=1, via=[seen])
    assert _steps(plan(start, _goal(2, {1: 0, 2: 0, 3: 0}, {1: 10, 2: 0, 3: 10}, **goal))) == [
        (1, "bet", 10.0, 10.0), (2, "fold", None, 0.0), (3, "call", 10.0, 10.0)]


def test_plan_raise_reopens_the_round():
    start = _round({1: 0, 2: 0, 3: 0, 4: 0}, STACKS, actor=1, button=4)
    seen = shape(1, {1: 10, 2: 0, 3: 30, 4: 0}, {2}, 4)   # второй уже сбросил, третий поднял до 30
    goal = dict(folded={1, 2}, actor=3, via=[seen])
    assert _steps(plan(start, _goal(2, {1: 0, 2: 0, 3: 0, 4: 0}, {1: 10, 2: 0, 3: 30, 4: 30}, **goal))) == [
        (1, "bet", 10.0, 10.0), (2, "fold", None, 0.0), (3, "raise", 30.0, 30.0),
        (4, "call", 30.0, 30.0), (1, "fold", None, 0.0)]   # рейз открыл круг: первый отвечает снова


def test_plan_headsup_blinds_and_the_big_blinds_option():
    """HU: кнопка докупает малый блайнд, большой имеет право слова — и ходит первым постфлоп."""
    start = _round({1: 1, 2: 2}, {1: 99.0, 2: 98.0}, actor=1, button=1, anchor=2, street=0)
    assert _steps(plan(start, _goal(1, {1: 0, 2: 0}, {1: 1, 2: 0}, actor=2))) == [
        (1, "call", 2.0, 1.0), (2, "check", None, 0.0)]


def test_plan_says_no_when_the_observation_admits_no_single_story():
    start = _round({1: 0, 2: 0, 3: 0}, STACKS, actor=1, button=3)
    assert plan(start, _goal(1, {1: 5, 2: 0, 3: 0}, {1: 0, 2: 0, 3: 0}, actor=2)) is None    # ставка из ничего
    assert plan(start, _goal(2, {1: 0, 2: 0, 3: 0}, {1: 0, 2: 5, 3: 0}, actor=1)) is None    # ответ без денег
    blind = _round({1: 0, 2: 0, 3: 0}, STACKS, actor=None, button=3)                          # чей ход не виден
    assert plan(blind, _goal(1, {1: 0, 2: 0, 3: 0}, {1: 0, 2: 0, 3: 0}, actor=None, known=False)) is None


def test_plan_fold_win_takes_the_pot_into_account():
    start = _round({1: 0, 2: 0, 3: 0}, STACKS, actor=1, button=3)
    goal = _goal(1, {1: 0, 2: 0, 3: 0}, {1: 10, 2: 0, 3: 0}, folded={2, 3}, over=True, pot_delta=10)
    assert _steps(plan(start, goal)) == [
        (1, "bet", 10.0, 10.0), (2, "fold", None, 0.0), (3, "fold", None, 0.0)]


def test_plan_takes_the_bet_from_a_witness_when_the_hand_ends():
    """Итог руки в одном интервале со ставкой: вложений не видно, сумму берёт из кадра-свидетеля."""
    start = _round({1: 0, 2: 0, 3: 0, 4: 0}, STACKS, actor=1, button=4, street=3)
    seen = [shape(3, {1: 62, 2: 0, 3: 0, 4: 0}, (), 2), shape(3, {1: 62, 2: 0, 3: 0, 4: 0}, {2}, 3)]
    goal = _goal(3, {1: 0, 2: 0, 3: 0, 4: 0}, None, folded={2}, over=True, pot_delta=186, via=seen)
    assert _steps(plan(start, goal)) == [
        (1, "bet", 62.0, 62.0), (2, "fold", None, 0.0), (3, "call", 62.0, 62.0), (4, "call", 62.0, 62.0)]


def test_plan_call_beats_a_raise_nobody_can_answer():
    """Ставивший олл-ин, третий сбросил: рейз от колла отличался бы лишь возвратом непокрытого."""
    start = _round({1: 11, 2: 0, 3: 0}, {1: 0.0, 2: 5.0, 3: 389.0}, actor=3, button=3, anchor=1, acted=True,
                   folded={2}, street=2, invested={1: 21.0, 2: 10.0, 3: 10.0})   # до терна все вложили по 10
    goal = _goal(3, {1: 0, 2: 0, 3: 0}, None, folded={2}, over=True, pot_delta=11)
    assert _steps(plan(start, goal)) == [(3, "call", 11.0, 11.0)]


def test_history_verdict_tells_equivalent_from_invented():
    """«Рейз, который некому было назвать», по кадрам неотличим от колла: это эквивалент, не выдумка."""
    posts = [(0, "preflop", 1, "post", 2.0, 2.0), (1, "preflop", 2, "post", 1.0, 1.0)]
    truth = posts + [(2, "preflop", 1, "raise", 50.0, 48.0),      # второй в олл-ине: 30 вернулись бы герою
                     (3, "preflop", 2, "call", 20.0, 19.0)]
    mine = posts + [(2, "preflop", 1, "call", 20.0, 18.0), (3, "preflop", 2, "call", 20.0, 19.0)]
    assert history_verdict(truth, truth, 0) == "точно"
    assert history_verdict(mine, truth, 0) == "эквивалент"
    called = posts + [(2, "preflop", 1, "raise", 50.0, 48.0),     # рейз называли: колл его не заменяет
                      (3, "preflop", 2, "call", 50.0, 49.0)]
    assert history_verdict(mine, called, 0) == "выдумано"
    assert history_verdict(posts, truth, 1) == "пропуск"
    other = posts + [(2, "preflop", 1, "bet", 20.0, 18.0), (3, "preflop", 2, "call", 20.0, 19.0)]
    assert history_verdict(other, mine, 0) == "иной рассказ"       # суммы те же, тип действия другой
    assert history_verdict(other, mine, 2) == "пропуск"            # трекер сам предупредил


def test_compare_hand_tells_a_tracked_hand_from_a_wrong_one(preset):
    """Сверка приёмок: отыгранная трекером рука совпадает с движком; испорченный срез — ловится."""
    tracker, session = Tracker(preset), _session(2)
    engine, rng = session.engine, random.Random(23)
    for _ in range(2):
        tracker.feed(frame_of(session.view()))
    while not engine.hand_over:                                 # рука доигрывается на глазах у трекера
        action = _choose(engine, rng)
        engine.apply(action.action, action.amount_to)
        for _ in range(2):
            tracker.feed(frame_of(session.view()))
    hand, view = engine.snapshot_for(WEB_HERO).model_dump(), session.view()
    assert view["hand_over"] and compare_hand(tracker, hand, view) == ([], "точно")

    judge, pot = Judge(), round(float(view["pot"]), 2)
    tracker.hand.pot += 1.0                                     # банк «не тот»
    tracker._snapshot = None
    errors, _ = judge.judge(tracker, hand, view)
    assert errors == [f"банк: правда {pot}, у трекера {pot + 1}"] and judge.confirmed == 1 and not judge.passed
    tracker.hand.pot -= 1.0
    tracker._snapshot = None                                    # срез снова верный: промах не забыт
    assert judge.judge(tracker, hand, view) == ([], "точно") and judge.confirmed == 2   # промах не забыт
    assert not judge.passed and "   рука 1: банк: правда 204.0, у трекера 205.0" in judge.report()


def test_tracker_view_is_a_json_summary(preset):
    tracker = Tracker(preset)
    assert json.dumps(tracker.view()) and tracker.view()["phase"] == "idle"
    play(tracker, _session(3), hands=1, rng=random.Random(2))
    view = json.loads(json.dumps(tracker.view()))     # для зеркала: чистый JSON
    assert view["phase"] == "over" and view["hand"] == 1 and view["street"] == "showdown"
    assert [s["name"] for s in view["seats"]] == ["hero", "pos_left", "pos_top", "pos_right"]
    assert view["seats"][0]["seat"] == 1 and view["gaps"] == 0 and view["actions"] == len(tracker.snapshot.actions)


# ---------- уровень кадра и граница «чистой логики» ----------

def test_level_of_reads_the_table_and_flags_unreliable(preset):
    session = _session(3)
    view = session.view()
    table = Table.from_preset(preset)
    level = level_of(frame_of(view), table)
    assert level.bad is None and level.pot == 3 and level.board == ()
    dealer = next((pos for pos, b in zip(_seated(view), view["bots"]) if b["seat"] == view["button_seat"]), "hero")
    assert level.button == dealer and level.over is False
    assert level.hero_buttons is (view["to_act"] == WEB_HERO)
    assert set(level.seats) == {"hero", *_seated(view)}
    low = frame_of(view)
    numbers = dict(low.numbers, **{"pot.amount": NumberReading("low", 3.0, "3", 0.4)})
    assert level_of(Perception(0, BLANK, low.cards, numbers, {}, low.seats, {}, low.flags, {}), table).bad == "pot.amount"


def test_preset_without_seat_order_is_refused(preset):
    del preset.zones["table"]
    with pytest.raises(ValueError, match="seat_order"):
        Table.from_preset(preset)


def test_serial_counts_states_not_frames(preset):
    """То же состояние в новых кадрах — не новое: serial растёт только на принятом состоянии.

    Так serial — номер состояния для пилота (одно решение на состояние), а
    changing — «отличие видно, но ещё не подтверждено»: стол меняется.
    """
    tracker, session = Tracker(preset), _session(2)
    kinds = lambda events: [e.kind for e in events]  # noqa: E731
    assert kinds(tracker.feed(frame_of(session.view()))) == []
    assert kinds(tracker.feed(frame_of(session.view()))) == ["hand_start", "hero_turn"]
    assert kinds(tracker.feed(frame_of(session.view())) + tracker.feed(frame_of(session.view()))) == []
    assert tracker.serial == 1 and tracker.stats["состояния подтверждены"] == 2 and not tracker.changing

    engine = session.engine
    action = next(la for la in engine.legal() if la.action in ("check", "call"))
    engine.apply(action.action, action.amount_to)         # герой сходил — трекеру покажут позже
    assert kinds(tracker.feed(frame_of(session.view()))) == [] and tracker.changing and tracker.serial == 1
    assert kinds(tracker.feed(frame_of(session.view()))) == ["actions"] and not tracker.changing
    assert tracker.serial == 2
    glitch = json.loads(json.dumps(session.view()))       # один сбойный кадр: отличие не подтверждается
    glitch["pot"] += 1.0
    tracker.feed(frame_of(glitch))
    assert tracker.changing
    tracker.feed(frame_of(session.view()))
    assert not tracker.changing and tracker.serial == 2


def test_tracker_needs_no_pixels_or_browser():
    """Граница закреплена тестом: импорт трекера и пилота не тянет в процесс ни Pillow, ни Selenium."""
    code = ("import sys; import pokafer.eyes.tracker, pokafer.eyes.level, pokafer.eyes.history, "
            "pokafer.eyes.pilot, pokafer.eyes.report; "
            "leak = sorted(m for m in sys.modules if m == 'PIL' or m.startswith('selenium')); "
            "assert not leak, leak")
    subprocess.run([sys.executable, "-c", code], check=True, cwd=Path(__file__).resolve().parents[1])
