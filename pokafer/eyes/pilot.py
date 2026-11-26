"""Пилот: боевой цикл героя за живым столом — кадры → трекер → мозг → актатор.

Герой играет только тем, что видят глаза: трекер подтверждает состояние
двумя одинаковыми кадрами (Tracker), мозг решает по его Snapshot (Brain),
актатор исполняет решение реальным кликом (Actuator). Пилот — память о
своих ходах между ними:

- **Одно решение на подтверждённое состояние** (Tracker.serial): пока
  трекер не принял новое состояние, повторно не решаем.
- **Клик обязан сработать.** После действия трекер обязан найти его в
  восстановленной истории — ровно то, что решил мозг; иначе это отмечается.
  Стол не изменился — клик повторяется ТЕМ ЖЕ решением (второго, другого,
  стол получить не должен); если виден кадр, что стол уже меняется, — ждём.
  Попыток не более max_tries, затем бой останавливается (Stopped): вслепую
  действовать нельзя.
- **История неточна — только фолд/чек/колл.** После пропуска трекер не
  знает минимального рейза (поле суммы с экрана не читается): рейзить
  нельзя, мозгу разрешены лишь пассивные действия.
- **Память об оппонентах:** оконченную руку мозг наблюдает целиком
  (Brain.observe) — профили считаются по полной истории.
- **Рейз, который некому назвать** (все остальные олл-ин), по кадрам
  неотличим от колла — пилот его так и играет.

Пилот — чистая логика между трекером, мозгом и актатором: пикселей,
браузера и DOM здесь нет. Актатор — любой объект с методами fold,
check_or_call, raise_to, next_hand.
"""
from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from pokafer.brain.api import Brain
from pokafer.core.legal import LegalAction, legal_actions
from pokafer.core.schema import ActionRecord, Decision, Snapshot
from pokafer.eyes.tracker import HERO, Event, Tracker

PASSIVE = ("fold", "check", "call")     # меню, когда история раздачи неточна
EPS = 0.005                             # допуск суммы: стол показывает центы


class Stopped(RuntimeError):
    """Бой остановлен: стол не отвечает на клики — вслепую действовать нельзя."""


@dataclass(frozen=True)
class Note:
    """Заметка пилота о своём ходе: feed складывает, забирают take_notes."""
    kind: str                            # decision | retry | mismatch | unverified | next_retry
    hand: int
    decision: Decision | None = None
    tries: int = 1
    got: ActionRecord | None = None      # что увидел трекер, когда клик исполнился иначе


@dataclass
class _Click:
    """Клик по кнопке, который трекер ещё не увидел."""
    serial: int                          # состояние, на котором решено
    hand: int
    gaps: int                            # пропусков в руке на момент клика
    decision: Decision
    before: int                          # сколько действий в истории было до клика
    all_in_to: float                     # «до» олл-ина: им играется решение на весь стек
    unread: int = 0                      # недостоверных кадров было на момент клика
    tries: int = 1
    at: float = 0.0                      # момент клика (по clock)


def _matches(want: Decision, got: ActionRecord | None) -> bool:
    """Действие в истории — то, что решил мозг (у колла сумма как выйдет: олл-ин меньше ставки)."""
    if got is None:
        return False
    if want.action == "call":
        return got.type == "call"
    return got.type == want.action and abs((got.amount_to or 0.0) - (want.amount_to or 0.0)) <= EPS


class Pilot:
    """Вести героя за столом: feed(perception) — подписчик цикла зрения (loop.watch)."""

    def __init__(self, tracker: Tracker, brain: Brain, actuator, *,
                 clock: Callable[[], float] = time.monotonic, retry_sec: float = 2.0,
                 max_tries: int = 3, on_decision: Callable[[Snapshot, Decision], None] | None = None,
                 next_hand: bool = True):
        self.tracker, self.brain, self.actuator = tracker, brain, actuator
        self.clock, self.retry_sec, self.max_tries = clock, retry_sec, max_tries
        self.next_hand = next_hand                # в руме есть кнопка следующей руки; сдаёт сам — False
        self.on_decision = on_decision    # о решении — до клика: тесты сверяют срез с движком
        self.hands = 0                    # рук сыграно (увиден итог)
        self.won = 0.0                    # исход героя: конец минус старт каждой руки (сброс стеков не мешает)
        self.thinking = 0.0               # сколько миллисекунд думал мозг (всего)
        self.stats: Counter = Counter()
        self.last: Decision | None = None # последнее решение — зеркалу
        self._notes: list[Note] = []
        self._click: _Click | None = None
        self._next: tuple[int, int, float] | None = None     # «следующая рука»: (рука, попыток, момент клика)

    # ---------- вход и выход ----------

    def feed(self, perception) -> list[Event]:
        """Один кадр: трекер читает стол, пилот действует по подтверждённому. Возврат — события трекера."""
        events = self.tracker.feed(perception)
        self._settle()
        if any(e.kind == "hand_over" for e in events):
            hand = self.tracker.hand
            self.hands += 1
            if hand.start is not None:                    # руки, виденной с раздачи: исход — по стекам
                self.won += round(hand.seats[HERO].stack - hand.start[HERO], 2)
            self.brain.observe(self.tracker.snapshot)       # рука целиком: профили — по полной истории
        if self.tracker.phase == "over":
            if self.next_hand:                    # рум, сдающий сам, new hand покажут кадры
                self._next_hand()
        elif self.tracker.hero_turn:
            self._take_turn()
        return events

    def take_notes(self) -> list[Note]:
        """Заметки с прошлого запроса (терминал, приёмка)."""
        notes, self._notes = self._notes, []
        return notes

    def view(self) -> dict:
        """Что думают трекер и пилот — в JSON (зеркало)."""
        return {**self.tracker.view(), "pilot": {
            "strategy": self.brain.strategy.name, "last": self.last.model_dump() if self.last else None,
            "hands": self.hands, "won": self.won, "thinking_ms": self.thinking, "stats": dict(self.stats)}}

    # ---------- ход героя ----------

    def _take_turn(self) -> None:
        """Ход героя подтверждён: новое состояние — решить и кликнуть, прежнее — повторить клик."""
        t, click = self.tracker, self._click
        if click is None or click.serial != t.serial:
            self._play()
        elif not t.changing and self.clock() - click.at >= self.retry_sec:
            if click.tries >= self.max_tries:
                raise Stopped(f"стол не реагирует на клики: рука {t.hand.no}, {click.tries} попыток; "
                              f"недостоверных кадров с первого клика: {self._unread() - click.unread} "
                              "(0 — клики не дошли; много — глаза не читают стол); бой остановлен")
            click.tries += 1
            self._press(click)
            self.stats["повторы"] += 1
            self._notes.append(Note("retry", t.hand.no, click.decision, click.tries))

    def _play(self) -> None:
        t = self.tracker
        snap, hand = t.snapshot, t.hand
        legal = legal_actions(snap)
        decision = self._decide(snap, legal)
        self.last = decision
        if self.on_decision is not None:
            self.on_decision(snap, decision)
        click = self._click = _Click(t.serial, hand.no, hand.gaps, decision, len(hand.actions),
                                     max((la.amount_to for la in legal if la.action == decision.action),
                                         default=0.0), unread=self._unread())
        self._press(click)
        self.stats["решения"] += 1
        self.stats[f"решение: {decision.action}"] += 1
        self.thinking += decision.elapsed_ms
        self._notes.append(Note("decision", hand.no, decision))

    def _decide(self, snap: Snapshot, legal: list[LegalAction]) -> Decision:
        """Решение, которое исполнимо: по неполной истории — без ставок; рейз без отвечающего — колл."""
        if self.tracker.exact and self.tracker.from_start:
            decision = self.brain.decide(snap)
        else:
            self.stats["решения по неполной истории"] += 1
            decision = self.brain.decide(snap, allowed=PASSIVE)
        if decision.action in ("bet", "raise") and not any(
                s.stack > EPS for s in snap.seats if not s.is_hero and not s.folded):
            self.stats["рейз без отвечающего — колл"] += 1
            decision = decision.model_copy(update={
                "action": "call", "amount_to": next(la.amount_to for la in legal if la.action == "call"),
                "distribution": {}, "reasoning": f"{decision.reasoning}; отвечать некому — колл"})
        return decision

    def _unread(self) -> int:
        """Сколько кадров трекер забраковал за всё время."""
        return sum(v for k, v in self.tracker.stats.items() if k.startswith("кадр недостоверный"))

    def _press(self, click: _Click) -> None:
        act, d = self.actuator, click.decision
        if d.action == "fold":
            act.fold()
        elif d.action in ("check", "call"):
            act.check_or_call()
        else:
            act.raise_to(d.amount_to, click.all_in_to)
        click.at = self.clock()

    def _settle(self) -> None:
        """Трекер принял новое состояние: найти в истории действие прошлого клика."""
        click, t = self._click, self.tracker
        if click is None or t.serial == click.serial:
            return                                       # стол ещё не изменился
        self._click = None
        got = next((a for a in t.hand.actions[click.before:] if a.seat == HERO), None)
        if t.hand.no != click.hand or t.hand.gaps != click.gaps:
            self.stats["исполнение не проверено"] += 1
            self._notes.append(Note("unverified", t.hand.no, click.decision))
        elif _matches(click.decision, got):
            self.stats["исполнено как решено"] += 1
        else:
            self.stats["исполнено иначе"] += 1
            self._notes.append(Note("mismatch", t.hand.no, click.decision, click.tries, got))

    def _next_hand(self) -> None:
        """Рука окончена: «следующая рука» — один клик на руку; не подействовал — повторить."""
        no, state = self.tracker.hand.no, self._next
        tries = 1
        if state is not None and state[0] == no:
            if self.tracker.changing or self.clock() - state[2] < self.retry_sec:
                return                                   # клик был недавно или стол уже меняется — ждём
            if state[1] >= self.max_tries:
                raise Stopped(f"кнопка «следующая рука» не отвечает: рука {no}, "
                              f"{state[1]} попыток — бой остановлен")
            tries = state[1] + 1
            self.stats["повторы: следующая рука"] += 1
            self._notes.append(Note("next_retry", no, tries=tries))
        else:
            self.stats["следующая рука"] += 1
        self.actuator.next_hand()
        self._next = (no, tries, self.clock())
