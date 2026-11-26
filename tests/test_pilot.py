"""Пилот без браузера: движок стола играет, «кнопки» — действия движка, клики теряются.

Пилот — память героя о своих ходах (pokafer/eyes/pilot.py): решает по
подтверждённому состоянию трекера, кликает и проверяет, что трекер увидел
именно решённое. Здесь стол — движок, кадры — какие глаза выдали бы с этого
состояния (tests/test_tracker.py), кнопки — прямой вызов движка, как у
страницы: глагол рейза — по тому, есть ли ставка на столе.
"""
import json
import random
from types import SimpleNamespace

import pytest

from pokafer.brain.api import Brain
from pokafer.brain.memory import InMemoryProfileStore
from pokafer.core.legal import legal_actions
from pokafer.core.schema import ActionRecord, Decision
from pokafer.eyes.pilot import PASSIVE, Pilot, Stopped, _matches
from pokafer.eyes.tracker import HERO, Tracker
from pokafer.eyes.report import describe_note
from pokafer.eyes.tracker import Tracker
from pokafer.eyes.truth import compare_hand, state_key
from pokafer.web.table_server import HERO as WEB_HERO

from test_tracker import _choose, _session, frame_of, preset  # noqa: F401 — preset: фикстура


class Clock:
    """Время теста: течёт только когда его подвинули."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Buttons:
    """Кнопки стола без браузера: клик — действие движка, как у страницы.

    lose — номера кликов, которые пропали (стол их не видел); lose_next —
    клики «следующая рука» не доходят; raise_as="min" — поле суммы не
    приняло ввод: уходит минимальный рейз.
    """

    def __init__(self, session, lose=(), lose_next: bool = False, raise_as: str = "given"):
        self.session, self.lose, self.lose_next, self.raise_as = session, frozenset(lose), lose_next, raise_as
        self.clicks = 0
        self.truths: list[tuple[dict, dict]] = []      # (раздача движка, вид) на итогах рук
        self.net = 0.0                                  # исход героя по движку: конец минус старт каждой руки

    def _press(self, act) -> None:
        self.clicks += 1
        if self.clicks not in self.lose:
            act()

    def fold(self):
        self._press(lambda: self.session.engine.apply("fold"))

    def check_or_call(self):
        engine = self.session.engine
        self._press(lambda: engine.apply("check" if any(la.action == "check" for la in engine.legal())
                                         else "call"))

    def raise_to(self, amount, all_in_to):
        engine = self.session.engine
        if self.raise_as == "min":
            amount = min(la.amount_to for la in engine.legal() if la.action in ("bet", "raise"))
        view = self.session.view()
        on_table = view["hero"]["bet"] > 0 or any(not b["folded"] and b["bet"] > 0 for b in view["bots"])

        def act():
            self.session.engine.apply("raise" if on_table else "bet", amount)

        self._press(act)

    def next_hand(self):
        start, finished = self.session.stacks[WEB_HERO], len(self.truths)
        if not self.lose_next:
            self._press(self._start)
        else:
            self.clicks += 1
        if len(self.truths) > finished:                 # клик дошёл: рука закрыта, стол пошёл дальше
            self.net += round(self.truths[-1][0]["seats"][0]["stack"] - start, 2)

    def _start(self):
        if self.session.engine.hand_over:
            self.truths.append((self.session.engine.snapshot_for(WEB_HERO).model_dump(), self.session.view()))
            self.session.settle_hand()
            self.session._start_hand()


class Scripted:
    """Мозг по сценарию: решения по очереди; кончились — чек/колл."""

    strategy = SimpleNamespace(name="сценарий")

    def __init__(self, *decisions: Decision):
        self.queue = list(decisions)

    def observe(self, snap) -> None:
        pass

    def decide(self, snap, allowed=None) -> Decision:
        if self.queue:
            return self.queue.pop(0)
        return Decision(action=next(la.action for la in legal_actions(snap) if la.action in ("check", "call")))


class Shover:
    """Бот-соперник: олл-ин, если можно."""

    def observe(self, snap) -> None:
        pass

    def decide(self, snap) -> Decision:
        jam = [la for la in legal_actions(snap) if la.action in ("bet", "raise")]
        return Decision(action=jam[-1].action, amount_to=jam[-1].amount_to) if jam else Scripted().decide(snap)


def fight(preset, buttons: Buttons, brain, **kw) -> tuple[Pilot, Clock]:
    """Пилот над новым трекером и данными кнопками; brain — стратегия по имени или готовый мозг."""
    clock = Clock()
    if isinstance(brain, str):
        brain = Brain(brain, memory=InMemoryProfileStore(), seed=11)
    return Pilot(Tracker(preset), brain, buttons, clock=clock, **kw), clock


def run(pilot: Pilot, clock: Clock, hands: int, tick: float = 0.0, notes=None,
        verdicts=("точно", "эквивалент"), check: bool = True) -> None:
    """Стол идёт, пока не сыграно hands рук: каждый шаг — одно состояние в двух
    кадрах, затем не более одного хода бота; итог руки (check) — если клик
    «следующая рука» дошёл — сверяется с движком."""
    session, seen = pilot.actuator.session, pilot.hands
    for _ in range(20000):
        if pilot.hands >= hands:
            return
        clock.now += tick
        view = session.view()
        for _ in range(2):                              # состояние видно в двух кадрах подряд
            pilot.feed(frame_of(view))
            if notes is not None:
                notes.extend(pilot.take_notes())
        if pilot.hands > seen:                          # рука окончена: её итог ещё не ушёл из трекера
            seen = pilot.hands
            if check and pilot.actuator.truths:
                errors, verdict = compare_hand(pilot.tracker, *pilot.actuator.truths[-1])
                assert not errors and verdict in verdicts, (errors, verdict)
        if state_key(session.view()) == state_key(view) and view["to_act"] not in (WEB_HERO, None):
            session.bot_step()                          # стол не ушёл с показанного: ход бота
    raise AssertionError("стол не доиграл руки: цикл не сходится")


# ---------- бой: каждую руку герой играет по подтверждённому состоянию ----------

@pytest.mark.parametrize("bots", [1, 3, 5])
def test_pilot_plays_every_hand_with_exactly_one_click_per_decision(preset, bots):
    buttons = Buttons(_session(bots))
    pilot, clock = fight(preset, buttons, "random")
    run(pilot, clock, hands=20)
    stats, memory = pilot.stats, pilot.brain.memory.profiles()
    assert pilot.hands == 20 and pilot.tracker.hand.no == 20
    assert stats["решения"] > 20
    assert all(stats[f"решение: {kind}"] for kind in ("fold", "check", "call", "bet", "raise"))
    assert stats["исполнено как решено"] == stats["решения"]
    assert stats["исполнено иначе"] == stats["исполнение не проверено"] == 0
    assert stats["повторы"] == stats["повторы: следующая рука"] == 0 and stats["следующая рука"] == 20
    assert buttons.clicks == stats["решения"] + 20         # ни одного лишнего клика
    assert {s.name for s in pilot.tracker.snapshot.seats} == set(memory)   # память — по оконченным рукам
    assert all(p["hands"] == 20 for p in memory.values())
    assert pilot.won == pytest.approx(buttons.net)


def test_lost_click_is_repeated_with_the_same_decision(preset):
    """Первый клик пропал: стол не изменился — после retry_sec клик повторяется ТЕМ ЖЕ решением."""
    buttons = Buttons(_session(2), lose={1})             # герой ходит первым: его первый клик потерян
    pilot, clock = fight(preset, buttons, "random", retry_sec=2.0)
    notes: list = []
    run(pilot, clock, hands=3, tick=1.0, notes=notes)
    first = next(n for n in notes if n.kind == "decision")
    retry = next(n for n in notes if n.kind == "retry")
    assert pilot.stats["повторы"] == 1 and retry.tries == 2
    assert retry.decision is first.decision and retry.hand == first.hand
    assert "повтор 2" in describe_note(retry)
    assert buttons.clicks == pilot.stats["решения"] + 3 + 1
    assert pilot.stats["исполнено как решено"] == pilot.stats["решения"]


def test_table_that_ignores_clicks_stops_the_fight(preset):
    """Стол не отвечает: max_tries кликов тем же решением — и стоп, вслепую дальше не играем."""
    buttons = Buttons(_session(1), lose=set(range(1, 100)))
    pilot, clock = fight(preset, buttons, "random", max_tries=3)
    with pytest.raises(Stopped, match="не реагирует"):
        run(pilot, clock, hands=1, tick=3.0)
    assert buttons.clicks == 3 and pilot.stats["решения"] == 1 and pilot.stats["повторы"] == 2


def test_next_hand_that_never_answers_stops_the_fight(preset):
    buttons = Buttons(_session(1), lose_next=True)
    pilot, clock = fight(preset, buttons, Scripted(Decision(action="fold")), max_tries=3)
    with pytest.raises(Stopped, match="следующая рука"):
        run(pilot, clock, hands=2, tick=3.0)
    assert pilot.hands == 1 and pilot.stats["следующая рука"] == 1 and pilot.stats["повторы: следующая рука"] == 2
    assert buttons.clicks == pilot.stats["решения"] + 3


def test_room_that_deals_by_itself_gets_no_next_hand_click(preset):
    """Рум сдаёт сам (next_hand=False): кнопки нет — пилот ждёт новую руку от кадров."""
    session = _session(1)
    buttons = Buttons(session)
    pilot, clock = fight(preset, buttons, Scripted(Decision(action="fold")), next_hand=False)
    for _ in range(4):                          # герой сбросил, итог руки принят
        pilot.feed(frame_of(session.view()))
    assert pilot.hands == 1 and pilot.tracker.phase == "over" and buttons.clicks == 1
    clock.now = 30.0                            # сколько ни жди — клика не будет
    for _ in range(2):
        pilot.feed(frame_of(session.view()))
    assert buttons.clicks == 1
    buttons._start()                            # рум сдал сам
    run(pilot, clock, hands=2, tick=30.0, check=False)
    assert pilot.hands == 2 and buttons.clicks == pilot.stats["решения"]      # ни одного клика «следующая рука»


def test_next_hand_is_clicked_once_per_hand_and_repeated_if_lost(preset):
    buttons = Buttons(_session(1), lose={2})             # герой сбрасывает — второй клик: «следующая рука»
    pilot, clock = fight(preset, buttons, Scripted(Decision(action="fold")), retry_sec=2.0)
    notes: list = []
    run(pilot, clock, hands=2, tick=1.0, notes=notes)
    assert pilot.stats["следующая рука"] == 2 and pilot.stats["повторы: следующая рука"] == 1
    assert [(n.kind, n.tries, n.hand) for n in notes if n.kind == "next_retry"] == [("next_retry", 2, 1)]
    assert buttons.clicks == pilot.stats["решения"] + 3


# ---------- что разрешено решать и что считается исполненным ----------

def test_incomplete_history_leaves_only_fold_check_call(preset):
    """Стол встречен посреди руки: история неполна, минимум рейза неизвестен — только фолд/чек/колл."""
    session = _session(2)
    rng = random.Random(5)
    while len(session.view()["board"]) < 3:             # префлоп играется без трекера
        action = _choose(session.engine, rng)
        session.engine.apply(action.action, action.amount_to)
    buttons = Buttons(session)
    pilot, clock = fight(preset, buttons, "random")
    decisions: list[tuple[str, str]] = []
    pilot.on_decision = lambda snap, d: decisions.append((snap.hand_id, d.action))
    run(pilot, clock, hands=5, verdicts=("точно", "эквивалент", "пропуск"))   # встреченная рука — с пропуском
    joined = [d for hand, d in decisions if hand == "h0001"]
    assert joined and set(joined) <= set(PASSIVE)
    assert any(d in ("bet", "raise") for hand, d in decisions if hand != "h0001")   # дальше — полное меню
    assert pilot.stats["решения по неполной истории"] == len(joined)


def test_raise_nobody_can_answer_is_played_as_a_call(preset):
    """Все остальные олл-ин: рейз по кадрам неотличим от колла — пилот так и играет."""
    session = _session(1)
    session.button, session.stacks = WEB_HERO, {1: 300.0, 2: 150.0}     # бот — кнопка (SB), ходит первым
    session._start_hand()
    session.brains[2] = Shover()                       # бот ставит олл-ин 150
    buttons = Buttons(session)
    pilot, clock = fight(preset, buttons, Scripted(Decision(action="raise", amount_to=300.0)))
    run(pilot, clock, hands=1)
    assert pilot.stats["решения"] == 1 and pilot.stats["рейз без отвечающего — колл"] == 1
    assert pilot.stats["исполнено как решено"] == 1 and pilot.last.action == "call"
    hero = [(a["seat"], a["type"]) for a in buttons.truths[-1][0]["actions"] if a["seat"] == WEB_HERO]
    assert hero == [(WEB_HERO, "post"), (WEB_HERO, "call")]


def test_click_that_did_something_else_is_reported(preset):
    """Поле суммы не приняло ввод: ушёл минимальный рейз — в истории не то, что решил мозг."""
    session = _session(1)
    legal = [(la.action, la.amount_to) for la in legal_actions(session.engine.snapshot_for(WEB_HERO))]
    assert session.engine.actor_seat == WEB_HERO and ("raise", 6.0) in legal and ("raise", 4.0) in legal
    buttons = Buttons(session, raise_as="min")
    pilot, clock = fight(preset, buttons, Scripted(Decision(action="raise", amount_to=6.0)))
    notes: list = []
    run(pilot, clock, hands=1, notes=notes)
    mismatch = next(n for n in notes if n.kind == "mismatch")
    assert pilot.stats["исполнено иначе"] == 1 and pilot.stats["исполнено как решено"] == 0
    assert (mismatch.decision.action, mismatch.decision.amount_to) == ("raise", 6.0)
    assert (mismatch.got.type, mismatch.got.amount_to) == ("raise", 4.0)
    assert "КЛИК ИСПОЛНИЛСЯ ИНАЧЕ" in describe_note(mismatch)


def test_execution_is_not_judged_through_a_gap(preset):
    """История руки с пропуском: сверять исполнение не с чем — честно «не проверено», а не «иначе»."""
    session = _session(1)
    buttons = Buttons(session, lose={1})                # клик героя потерян: состояние то же
    pilot, clock = fight(preset, buttons, Scripted(Decision(action="raise", amount_to=6.0)))
    for _ in range(2):
        pilot.feed(frame_of(session.view()))
    assert pilot.stats["решения"] == 1 and session.engine.actor_seat == WEB_HERO
    pilot.tracker.hand.gaps += 1                        # между кликом и следующим кадром — пропуск
    buttons.lose, clock.now = frozenset(), 5.0          # прошло больше retry_sec: клик повторяется и доходит
    for _ in range(2):
        pilot.feed(frame_of(session.view()))
    for _ in range(2):
        pilot.feed(frame_of(session.view()))
    assert pilot.stats["повторы"] == 1
    assert pilot.stats["исполнение не проверено"] == 1 and pilot.stats["исполнено иначе"] == 0


def test_no_retry_while_the_table_is_changing(preset):
    """Отличие от принятого состояния видно, но ещё не подтверждено: стол меняется — клик не повторяем."""
    session = _session(1)
    buttons = Buttons(session, lose_next=True)
    pilot, clock = fight(preset, buttons, Scripted(Decision(action="fold")))
    for _ in range(4):                      # герой сбросил, итог принят, «следующая рука» кликнута и потеряна
        pilot.feed(frame_of(session.view()))
    assert pilot.hands == 1 and buttons.clicks == 2 and not pilot.tracker.changing
    clock.now = 10.0                        # больше retry_sec — пора бы повторить
    later = json.loads(json.dumps(session.view()))
    later["pot"] += 1.0                     # кадр-свидетель: итог руки дорисовывается
    pilot.feed(frame_of(later))
    assert pilot.tracker.changing and buttons.clicks == 2      # ждём подтверждения
    for _ in range(2):
        pilot.feed(frame_of(session.view()))
    assert buttons.clicks == 3                               # отличие не подтвердилось — повтор


def test_a_record_matches_the_decision_only_when_it_is_that_action():
    def record(kind, to=None):
        return ActionRecord(order=0, street="flop", seat=HERO, type=kind, amount_to=to)

    assert _matches(Decision(action="check"), record("check"))
    assert not _matches(Decision(action="check"), record("call"))
    assert _matches(Decision(action="call"), record("call", 7.5))           # колл: сумма как выйдет
    assert not _matches(Decision(action="call"), record("raise", 7.5))
    assert _matches(Decision(action="raise", amount_to=12.0), record("raise", 12.0))
    assert not _matches(Decision(action="raise", amount_to=12.0), record("raise", 14.0))
    assert not _matches(Decision(action="fold"), None)                      # действия героя в истории нет


def test_summary_tells_the_fight_in_lines(preset):
    from pokafer.eyes.report import summary
    buttons = Buttons(_session(1))
    pilot, clock = fight(preset, buttons, "random")
    run(pilot, clock, hands=3)
    s = pilot.stats
    lines = summary(pilot)
    assert lines[0] == f"рук сыграно: 3; исход героя по стекам: {pilot.won:+g}"
    assert f"решений: {s['решения']} (" in lines[1]
    assert f"исполнено как решено: {s['исполнено как решено']}" in lines[2]
    assert f"повторов кликов: {s['повторы']}" in lines[3] and "недостоверных кадров" in lines[4]


def test_pilot_view_is_a_json_summary(preset):
    buttons = Buttons(_session(1))
    pilot, clock = fight(preset, buttons, "random")
    view = json.loads(json.dumps(pilot.view()))
    assert view["pilot"] == {"strategy": "random", "last": None, "hands": 0, "won": 0.0, "thinking_ms": 0.0,
                            "stats": {}}
    run(pilot, clock, hands=2)
    view = json.loads(json.dumps(pilot.view()))
    assert view["pilot"]["hands"] == 2 and view["pilot"]["last"]["strategy"] == "random"
    assert view["pilot"]["stats"]["решения"] == pilot.stats["решения"] and view["pilot"]["won"] == pilot.won
