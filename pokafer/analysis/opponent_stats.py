"""Поведенческая статистика игроков по историям раздач.

Классика покерных трекеров: VPIP (как часто добровольно входит в банк),
PFR (как часто рейзит префлоп), AF (агрессия постфлоп), Fold-vs-Bet.
Эти же числа — фичи для слоя модели оппонента (слой 2 «мозга»).
"""
from __future__ import annotations

from collections.abc import Iterable

from pokafer.core.schema import Snapshot

_EPS = 1e-9


def stats_from_snapshots(snapshots: Iterable[Snapshot]) -> dict[str, dict]:
    """Последний (самый полный) снапшот на раздачу -> профили игроков."""
    hands: dict[tuple[str, str], Snapshot] = {}
    for s in snapshots:
        key = (s.table_id, s.hand_id)
        if key not in hands or len(s.actions) >= len(hands[key].actions):
            hands[key] = s
    return stats_from_hands(hands.values())


def stats_from_hands(hands: Iterable[Snapshot]) -> dict[str, dict]:
    acc: dict[str, dict] = {}

    def player(name: str) -> dict:
        return acc.setdefault(
            name,
            {
                "hands": 0, "vpip": 0, "pfr": 0,
                "post_bets": 0, "post_calls": 0,
                "face_bet": 0, "fold_vs_bet": 0,
            },
        )

    for hand in hands:
        participants = {s.name for s in hand.seats}
        for name in participants:
            player(name)["hands"] += 1

        bet_street: dict[int, float] = {s.seat: 0.0 for s in hand.seats}
        seat_name = {s.seat: s.name for s in hand.seats}
        for a in sorted(hand.actions, key=lambda x: x.order):
            name = seat_name[a.seat]
            p = player(name)
            cur_max = max(bet_street.values(), default=0.0)
            facing = cur_max > bet_street[a.seat] + _EPS
            preflop = a.street == "preflop"

            if a.type == "post":
                bet_street[a.seat] = a.amount_to or 0.0
                continue
            if a.type == "fold":
                if not preflop and facing:
                    p["face_bet"] += 1
                    p["fold_vs_bet"] += 1
            elif a.type == "call":
                if preflop:
                    p["vpip"] += 1
                else:
                    p["post_calls"] += 1
                    if facing:
                        p["face_bet"] += 1
            elif a.type in ("bet", "raise"):
                if preflop:
                    p["vpip"] += 1
                    p["pfr"] += 1
                else:
                    p["post_bets"] += 1
            if a.amount_to is not None:
                bet_street[a.seat] = a.amount_to

    out: dict[str, dict] = {}
    for name, p in acc.items():
        h = max(p["hands"], 1)
        af = (
            p["post_bets"] / p["post_calls"]
            if p["post_calls"]
            else (float(p["post_bets"]) if p["post_bets"] else 0.0)
        )
        out[name] = {
            "hands": p["hands"],
            "vpip": p["vpip"] / h,
            "pfr": p["pfr"] / h,
            "af": round(af, 2),
            "fold_vs_bet": p["fold_vs_bet"] / max(p["face_bet"], 1),
            "face_bet_n": p["face_bet"],
        }
    return out
