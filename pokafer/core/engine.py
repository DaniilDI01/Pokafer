"""Движок раздачи NLHE: источник Snapshot'ов для живой игры и тестов.

Упрощения v1 (задокументированы):
  - сайд-поты не рассчитываются (весь банк уходит лучшей руке) — для HU-демо
    и поведенческого анализа не критично, отмечено в ROADMAP;
  - анте не раздаётся (поле есть в схеме, поддержим позже).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from pokafer.core.cards import card_str, evaluate7, hand_name, hand_name_ru, parse_card
from pokafer.core.legal import LegalAction, legal_actions
from pokafer.core.schema import ActionRecord, SeatState, Snapshot

STREETS = ["preflop", "flop", "turn", "river", "showdown"]
_STREET_CARDS = {"flop": 3, "turn": 1, "river": 1}


@dataclass
class _Seat:
    seat: int
    name: str
    stack: float
    hole: list[int] = field(default_factory=list)
    bet_street: float = 0.0
    invested: float = 0.0
    folded: bool = False
    all_in: bool = False

    @property
    def active(self) -> bool:
        return not self.folded


class HandEngine:
    """Одна раздача. Гоняет улицы, валидирует действия, считает шоудаун."""

    def __init__(
        self,
        seat_stacks: dict[int, float],
        names: dict[int, str] | None = None,
        button_seat: int | None = None,
        sb: float = 1.0,
        bb: float = 2.0,
        seed: int | None = None,
        hand_id: str = "h0001",
        table_id: str = "default",
    ):
        if len(seat_stacks) < 2:
            raise ValueError("Нужно минимум 2 места")
        self.seats: dict[int, _Seat] = {
            s: _Seat(s, (names or {}).get(s, f"p{s}", ), v)
            for s, v in seat_stacks.items()
        }
        self.order: list[int] = sorted(self.seats)
        self.sb, self.bb = float(sb), float(bb)
        self.hand_id, self.table_id = hand_id, table_id
        self.rng = random.Random(seed)

        self.button = button_seat if button_seat is not None else self.order[-1]
        self.deck = list(range(52))
        self.rng.shuffle(self.deck)
        self.board: list[int] = []
        self.street = "preflop"
        self.actions: list[ActionRecord] = []
        self.hand_over = False
        self.result: dict | None = None

        self._acted: set[int] = set()
        self._actor: int | None = None

        self._deal_holes()
        self._post_blinds()
        self._actor = self._first_to_act_preflop()
        if self._betting_impossible():
            self._runout_and_showdown()

    # ---------- публичный интерфейс ----------

    @property
    def actor_seat(self) -> int:
        if self._actor is None:
            raise RuntimeError("Ходить некому — раздача завершена")
        return self._actor

    def snapshot_for(self, seat: int) -> Snapshot:
        """Snapshot с точки зрения seat: видны только его карты."""
        me = self.seats[seat]
        pot = sum(s.invested for s in self.seats.values())
        return Snapshot(
            hand_id=self.hand_id,
            table_id=self.table_id,
            sb=self.sb,
            bb=self.bb,
            button_seat=self.button,
            actor_seat=seat,
            street=self.street,
            board=[card_str(c) for c in self.board],
            pot=pot,
            seats=[
                SeatState(
                    seat=s.seat,
                    name=s.name,
                    stack=s.stack,
                    bet_street=s.bet_street,
                    invested=s.invested,
                    folded=s.folded,
                    all_in=s.all_in,
                    is_hero=(s.seat == seat),
                    hole_cards=[card_str(c) for c in s.hole] if s.seat == seat else None,
                )
                for s in (self.seats[i] for i in self.order)
            ],
            actions=list(self.actions),
        )

    def legal(self, seat: int | None = None) -> list:
        seat = self.actor_seat if seat is None else seat
        return legal_actions(self.snapshot_for(seat))

    def apply(self, action: str, amount_to: float | None = None) -> None:
        """Применить действие текущего игрока. action: fold/check/call/bet/raise."""
        if self.hand_over:
            raise RuntimeError("Раздача уже завершена")
        actor = self.actor_seat
        me = self.seats[actor]
        legal = self.legal(actor)
        if action in ("fold", "check", "call"):
            match = [l for l in legal if l.action == action]
            if not match:
                raise ValueError(
                    f"Нелегальное действие {action!r}. Легальные: {[l.key for l in legal]}"
                )
            la = match[0]
        elif action in ("bet", "raise"):
            same = [l for l in legal if l.action == action]
            if not same or amount_to is None:
                raise ValueError(
                    f"Нелегальное действие {action!r} {amount_to}. "
                    f"Легальные: {[l.key for l in legal]}"
                )
            mn = min(l.amount_to for l in same)
            mx = max(l.amount_to for l in same)
            if not (mn - 1e-6 <= amount_to <= mx + 1e-6):
                raise ValueError(f"Сумма {amount_to:g} вне диапазона [{mn:g}, {mx:g}]")
            la = LegalAction(action, round(float(amount_to), 2))
        else:
            raise ValueError(f"Неизвестное действие {action!r}")

        if la.action == "fold":
            me.folded = True
            self._mark_action(actor, "fold", amount_to=None, amount=0.0)
            self._acted.add(actor)
            self._after_action(actor)
            return
        if la.action == "check":
            self._mark_action(actor, "check", amount_to=None, amount=0.0)
            self._acted.add(actor)
            self._after_action(actor)
            return
        if la.action == "call":
            add = min((la.amount_to or 0.0) - me.bet_street, me.stack)
            self._move_chips(me, add)
            self._mark_action(actor, "call", amount_to=me.bet_street, amount=add)
            self._acted.add(actor)
            self._after_action(actor)
            return
        # bet/raise
        add = (la.amount_to or 0.0) - me.bet_street
        self._move_chips(me, add)
        self._mark_action(
            actor, la.action, amount_to=me.bet_street, amount=add
        )
        self._acted = {actor}  # повышение: остальные должны ответить
        self._after_action(actor)

    # ---------- внутреннее ----------

    def _move_chips(self, seat: _Seat, amount: float) -> None:
        amount = round(max(0.0, min(amount, seat.stack)), 4)
        seat.stack -= amount
        seat.bet_street += amount
        seat.invested += amount
        if seat.stack <= 1e-9:
            seat.stack = 0.0
            seat.all_in = True

    def _mark_action(self, seat: int, type_: str, amount_to: float | None, amount: float) -> None:
        self.actions.append(
            ActionRecord(
                order=len(self.actions),
                street=self.street,
                seat=seat,
                type=type_,
                amount_to=round(amount_to, 4) if amount_to is not None else None,
                amount=amount,
            )
        )

    def _deal_holes(self) -> None:
        for _ in range(2):
            for s in self.order:
                self.seats[s].hole.append(self.deck.pop())

    def _post_blinds(self) -> None:
        hu = len(self.order) == 2
        sb_seat = self.button if hu else self._next(self.button)
        bb_seat = self._next(sb_seat)
        for seat, blind in ((sb_seat, self.sb), (bb_seat, self.bb)):
            s = self.seats[seat]
            add = min(blind, s.stack)
            self._move_chips(s, add)
            self._mark_action(seat, "post", amount_to=s.bet_street, amount=add)

    def _next(self, seat: int) -> int:
        i = self.order.index(seat)
        return self.order[(i + 1) % len(self.order)]

    def _next_eligible(self, seat: int) -> int | None:
        for _ in range(len(self.order)):
            seat = self._next(seat)
            s = self.seats[seat]
            if s.active and not s.all_in and seat not in self._acted:
                return seat
        return None

    def _first_to_act_preflop(self) -> int:
        if len(self.order) == 2:
            return self.button  # HU: баттон = SB, ходит первым на префлопе
        bb = self._next(self._next(self.button))
        return self._next(bb)

    def _first_to_act_postflop(self) -> int | None:
        return self._next_eligible(self.button)

    def _can_act_seats(self) -> list[int]:
        return [
            s.seat for s in (self.seats[i] for i in self.order)
            if s.active and not s.all_in
        ]

    def _betting_impossible(self) -> bool:
        return len(self._can_act_seats()) <= 1 or sum(
            1 for s in self.seats.values() if s.active
        ) <= 1

    def _after_action(self, actor: int) -> None:
        active = [s for s in self.seats.values() if s.active]
        if len(active) == 1:
            self._finish_by_fold(active[0].seat)
            return
        actors = self._can_act_seats()
        bets_equal = len({self.seats[s].bet_street for s in actors}) <= 1
        if set(actors) <= self._acted and bets_equal:
            self._close_street()
        else:
            nxt = self._next_eligible(actor)
            if nxt is None:
                self._close_street()
            else:
                self._actor = nxt

    def _close_street(self) -> None:
        self._acted = set()
        for s in self.seats.values():
            s.bet_street = 0.0
        if self.street == "river":
            self._showdown()
            return
        self._deal_next_street()
        if self._betting_impossible():
            self._runout_and_showdown()
            return
        nxt = self._first_to_act_postflop()
        if nxt is None:
            self._runout_and_showdown()
            return
        self._actor = nxt

    def _deal_next_street(self) -> None:
        self.street = STREETS[STREETS.index(self.street) + 1]
        for _ in range(_STREET_CARDS[self.street]):
            self.board.append(self.deck.pop())

    def _runout_and_showdown(self) -> None:
        while self.street != "river":
            self._deal_next_street()
        self._showdown()

    def _pot(self) -> float:
        return sum(s.invested for s in self.seats.values())

    def _finish_by_fold(self, winner: int) -> None:
        pot = self._pot()
        self.seats[winner].stack += pot
        self.street = "showdown"
        self.hand_over = True
        self._actor = None
        self.result = {
            "type": "fold_win",
            "winners": [winner],
            "pot": pot,
            "amount": pot,
        }

    def _showdown(self) -> None:
        pot = self._pot()
        active = [s for s in self.seats.values() if s.active]
        scored = {
            s.seat: evaluate7(s.hole + self.board) for s in active
        }
        best = max(scored.values())
        winners = [seat for seat, sc in scored.items() if sc == best]
        share = round(pot / len(winners), 4)
        for w in winners:
            self.seats[w].stack += share
        self.street = "showdown"
        self.hand_over = True
        self._actor = None
        self.result = {
            "type": "showdown",
            "winners": winners,
            "pot": pot,
            "amount": share,
            "scores": {seat: hand_name(sc) for seat, sc in scored.items()},
            "scores_ru": {seat: hand_name_ru(sc) for seat, sc in scored.items()},
            "hole_cards": {
                s.seat: [card_str(c) for c in s.hole] for s in active
            },
        }
