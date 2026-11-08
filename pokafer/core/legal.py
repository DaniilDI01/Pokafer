"""Легальные действия из Snapshot: мозг сам выводит, что можно делать.

Помимо fold/check/call возвращается «меню» сайзингов ставки/рейза:
мин-рейз, стандартные сайзинги (доли банка / кратные ставке) и олл-ин.
Стратегия выбирает из меню — так смешанная стратегия живёт в одном формате.
"""
from __future__ import annotations

from dataclasses import dataclass

from pokafer.core.schema import Snapshot


@dataclass(frozen=True)
class LegalAction:
    action: str  # fold | check | call | bet | raise
    amount_to: float | None = None  # для bet/raise/call — «до»

    @property
    def key(self) -> str:
        if self.amount_to is not None:
            return f"{self.action}:{self.amount_to:g}"
        return self.action

    def describe(self) -> str:
        if self.action in ("bet", "raise") and self.amount_to is not None:
            return f"{self.action} to {self.amount_to:g}"
        return self.action


def legal_actions(snap: Snapshot) -> list[LegalAction]:
    """Легальные действия для actor_seat: fold/check/call + меню ставок."""
    me = next(s for s in snap.seats if s.seat == snap.actor_seat)
    active = [s for s in snap.seats if not s.folded]
    max_bet = max(s.bet_street for s in active)
    to_call = max(0.0, max_bet - me.bet_street)
    my_all_in_to = me.bet_street + me.stack

    out: list[LegalAction] = []

    if to_call > 1e-9:
        out.append(LegalAction("fold"))
        out.append(LegalAction("call", min(max_bet, my_all_in_to)))
    else:
        out.append(LegalAction("check"))

    last_raise = _last_raise_size(snap)
    min_increment = max(last_raise, snap.bb)
    min_raise_to = max_bet + min_increment

    if my_all_in_to > max_bet + min_increment - 1e-9 and me.stack > 1e-9:
        verb = "bet" if max_bet <= 1e-9 else "raise"
        pot_base = snap.pot - me.bet_street  # банк без текущей ставки actor'а
        if verb == "bet":
            raw = [min_raise_to] + [pot_base * f for f in (0.5, 0.75, 1.0)]
        else:
            raw = [
                min_raise_to,
                max_bet * 2.5,
                max_bet + pot_base + to_call,  # «рейз размером с банк»
            ]
        seen: list[float] = []
        for amt in sorted(raw):
            amt = round(min(max(amt, min_raise_to), my_all_in_to), 2)
            if amt <= max_bet + 1e-9:
                continue
            if not seen or all(abs(amt - x) > max(0.01, amt * 0.02) for x in seen):
                seen.append(amt)
                out.append(LegalAction(verb, amt))
        # олл-ин доступен всегда (даже если сумма близка к другой)
        if my_all_in_to > max_bet + 1e-9 and (
            not seen or my_all_in_to > seen[-1] + 1e-9
        ):
            out.append(LegalAction(verb, round(my_all_in_to, 2)))
    return out


def _last_raise_size(snap: Snapshot) -> float:
    """Размер последнего повышения на текущей улице (для min-рейза)."""
    prev = 0.0
    last_inc = 0.0
    for a in snap.actions:
        if a.street != snap.street:
            continue
        if a.type in ("bet", "raise") and a.amount_to is not None:
            last_inc = a.amount_to - prev
            prev = a.amount_to
        elif a.type == "post" and a.amount_to:
            prev = max(prev, a.amount_to)
    return max(last_inc, 0.0)
