#!/usr/bin/env python3
"""Демо: сыграть руку против мозга в терминале (заказчикам показывать отсюда).

    python scripts/demo_cli.py                              # мозг локально
    python scripts/demo_cli.py --url http://127.0.0.1:8000  # через HTTP-сервис
    python scripts/demo_cli.py --record data/snapshots/demo.jsonl

Цвета включаются в живом терминале; NO_COLOR=1 выключает, POKAFER_COLOR=1 включает принудительно.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

from pokafer.adapters.replay_file import ReplayFile
from pokafer.brain.api import Brain
from pokafer.brain.memory import InMemoryProfileStore
from pokafer.core.cards import evaluate7, hand_name_ru, parse_card
from pokafer.core.engine import HandEngine
from pokafer.core.schema import Snapshot

HERO, VILLAIN = 1, 2
NAMES = {HERO: "Вы", VILLAIN: "Мозг"}

HELP_TXT = "f=фолд  c=колл/чек  b 6=ставка/рейз до 6  a=олл-ин  h=подсказка  q=выход"
STREET_RU = {
    "preflop": "ПРЕФЛОП",
    "flop": "ФЛОП",
    "turn": "ТЕРН",
    "river": "РИВЕР",
    "showdown": "ШОУДАУН",
}
_SUITS = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("POKAFER_COLOR"):
        return True
    return sys.stdout.isatty()


USE_COLOR = _color_enabled()
RESET, BOLD, RED, GREEN, YELLOW, CYAN, DIM = (
    "0", "1", "31", "32", "33", "36", "2",
)


def paint(text: str, *codes: str) -> str:
    if not USE_COLOR or not codes:
        return text
    return f"\033[{';'.join(codes)}m{text}\033[0m"


def fmt_card(card: str) -> str:
    """'Ah' -> 'A♥' (красным)."""
    suit = _SUITS.get(card[1], card[1])
    s = card[0] + suit
    return paint(s, RED) if card[1] in ("h", "d") else s


def fmt_cards(cards: list[str] | None) -> str:
    return " ".join(fmt_card(c) for c in cards) if cards else "—"


def human_action(key: str) -> str:
    """'raise:6' -> 'рейз до 6', 'call:2' -> 'колл(2)'."""
    verb, _, amt = key.partition(":")
    return {
        "fold": "фолд",
        "check": "чек",
        "call": f"колл({amt})" if amt else "колл",
        "bet": f"ставка {amt}" if amt else "ставка",
        "raise": f"рейз до {amt}" if amt else "рейз",
    }.get(verb, key)


def fmt_dist(dist: dict[str, float], top: int = 3) -> str:
    items = sorted(dist.items(), key=lambda kv: -kv[1])[:top]
    return " · ".join(f"{human_action(k)} {v:.0%}" for k, v in items)


def street_banner(snap: Snapshot, width: int = 46) -> None:
    title = f"{STREET_RU[snap.street]} · банк {snap.pot:g}"
    line = "─" * max(0, width - len(title) - 2)
    print(paint(f"\n── {title} {line}", BOLD))
    if snap.board:
        print(f"   борд:  {fmt_cards(snap.board)}")


def hero_combo(snap: Snapshot) -> str | None:
    """Текущая комбинация героя (подсказка для человека)."""
    me = next(s for s in snap.seats if s.seat == snap.actor_seat)
    if not me.hole_cards or not snap.board:
        return None
    cards = [parse_card(c) for c in list(me.hole_cards) + list(snap.board)]
    return hand_name_ru(evaluate7(cards))


def remote_decide(url: str, snap: Snapshot, strategy: str | None) -> dict:
    payload = {"snapshot": snap.model_dump()}
    if strategy:
        payload["strategy"] = strategy
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/decide",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def decide(args, brain: Brain | None, snap: Snapshot) -> dict:
    if args.url:
        return remote_decide(args.url, snap, args.strategy)
    d = brain.decide(snap)
    out = d.model_dump()
    out["decision"] = d.key()
    return out


def short_reason(decision: dict) -> str:
    reason = (decision.get("reasoning") or "").split(";")[0].strip()
    return f"  {reason}" if reason else ""


def render(snap: Snapshot, legal: list) -> None:
    me = next(s for s in snap.seats if s.seat == snap.actor_seat)
    opp = next(s for s in snap.seats if s.seat != snap.actor_seat)
    max_bet = max(s.bet_street for s in snap.seats if not s.folded)
    to_call = max(0.0, max_bet - me.bet_street)

    if opp.folded:
        opp_line = "   Мозг   сбросил"
    else:
        opp_bet = f" · ставка {opp.bet_street:g}" if opp.bet_street > 1e-9 else ""
        opp_line = f"   Мозг   стек {opp.stack:g}{opp_bet} · карты закрыты"
    print(paint(opp_line, DIM))

    combo = hero_combo(snap)
    me_bet = f" · ставка {me.bet_street:g}" if me.bet_street > 1e-9 else ""
    combo_s = f" · у вас: {combo}" if combo else ""
    print(f"   {paint('Вы', BOLD)}     [{fmt_cards(me.hole_cards)}] стек {me.stack:g}{me_bet}{combo_s}")

    parts = []
    if any(la.action == "fold" for la in legal):
        parts.append("f=фолд")
    if any(la.action == "check" for la in legal):
        parts.append("c=чек")
    call_la = next((la for la in legal if la.action == "call"), None)
    if call_la:
        cost = call_la.amount_to - me.bet_street
        odds = cost / (snap.pot + cost)
        parts.append(f"c=колл {cost:g} (оддсы {odds:.0%})")
    raises = [la for la in legal if la.action in ("bet", "raise")]
    if raises:
        lo, hi = raises[0].amount_to, raises[-1].amount_to
        verb = "рейз" if raises[0].action == "raise" else "ставка"
        parts.append(f"b X={verb} до X ({lo:g}–{hi:g})")
        parts.append(f"a=олл-ин {hi:g}")
    print("   " + paint(" | ".join(parts), YELLOW))


def ask(prompt: str) -> str:
    """input() с аккуратным выходом по EOF (Ctrl+D) / Ctrl+C."""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print("\n— ввод закрыт, выход —")
        sys.exit(0)


def parse_user_cmd(cmd: str, legal: list) -> tuple[str, float | None]:
    toks = cmd.split()
    if not toks:
        raise ValueError(f"пустой ввод. {HELP_TXT}")
    base = toks[0]
    if base in ("f", "fold"):
        return "fold", None
    if base in ("c", "call", "check"):
        return ("call", None) if any(l.action == "call" for l in legal) else ("check", None)
    if base in ("b", "r", "bet", "raise"):
        if len(toks) < 2:
            raise ValueError("укажите сумму: b 6")
        amount = float(toks[1].replace(",", "."))
        verb = "bet" if any(l.action == "bet" for l in legal) else "raise"
        lo = min(l.amount_to for l in legal if l.action == verb)
        hi = max(l.amount_to for l in legal if l.action == verb)
        if amount < lo:
            raise ValueError(f"ниже мин-рейза: минимум {lo:g} (b {lo:g})")
        if amount > hi:
            raise ValueError(f"выше олл-ина: максимум {hi:g} (a)")
        return verb, amount
    if base in ("a", "allin"):
        br = [l for l in legal if l.action in ("bet", "raise")]
        if br:
            return br[-1].action, max(l.amount_to for l in br)
        return "call", None
    raise ValueError(f"не понял {base!r}. {HELP_TXT}")


def print_result(res: dict) -> None:
    print()
    if res.get("type") == "showdown":
        print(paint(f"★ ШОУДАУН · банк {res['pot']:g}", BOLD))
        holes = res.get("hole_cards", {})
        for seat in sorted(res.get("scores_ru", res["scores"])):
            win = seat in res["winners"]
            cards = fmt_cards(holes.get(seat))
            name = res.get("scores_ru", res["scores"])[seat]
            line = f"   {NAMES[seat]:5s} [{cards}] {name}" + (paint("  ← ПОБЕДА", GREEN, BOLD) if win else "")
            print(paint(line, GREEN) if win else paint(line, DIM))
        if len(res["winners"]) > 1:
            print(paint(f"   сплит: по {res['amount']:g}", DIM))
    else:
        winner = NAMES[res["winners"][0]]
        print(paint(f"★ {winner} забирает {res['pot']:g} — оппонент сбросил", BOLD))


def play_hand(args, brain: Brain | None, stacks: dict[int, float], button: int, hand_no: int) -> dict[int, float]:
    engine = HandEngine(
        seat_stacks=dict(stacks),
        names=NAMES,
        button_seat=button,
        sb=args.sb,
        bb=args.bb,
        hand_id=f"h{hand_no:04d}",
        table_id="demo-table",
    )
    last_street = None
    while not engine.hand_over:
        actor = engine.actor_seat
        snap = engine.snapshot_for(actor)
        legal = engine.legal()
        if snap.street != last_street:
            last_street = snap.street
            street_banner(snap)
        if actor == HERO:
            render(snap, legal)
            while True:
                try:
                    cmd = ask(paint("▶ ", BOLD)).strip()
                    if cmd in ("q", "quit", "exit"):
                        sys.exit(0)
                    if cmd in ("h", "help", "?"):
                        d = decide(args, brain, snap)
                        print(f"   💡 мозг: {human_action(d['decision'])} — {fmt_dist(d['distribution'])}")
                        continue
                    action, amount = parse_user_cmd(cmd, legal)
                    engine.apply(action, amount)
                    key = f"{action}:{amount:g}" if amount is not None else action
                    print(paint(f"   ✔ Вы: {human_action(key)}", GREEN))
                    break
                except ValueError as e:
                    print(f"   !! {e}")
        else:
            d = decide(args, brain, snap)
            print(paint(
                f"   ▶ Мозг: {human_action(d['decision'])}"
                f"   [{fmt_dist(d['distribution'])}]{short_reason(d)}",
                CYAN,
            ))
            engine.apply(d["action"], d["amount_to"])
        if args.record:
            ReplayFile.append(args.record, snap, d if actor == VILLAIN else None)

    print_result(engine.result or {})

    if brain is not None and brain.memory is not None:
        profiles = brain.memory.profiles()
        if profiles:
            lines = [
                f"     {name}: {p['hands']} рук, VPIP {p['vpip']:.0%}, PFR {p['pfr']:.0%}, "
                f"AF {p['af']}, фолд-vs-бет {p['fold_vs_bet']:.0%}"
                for name, p in sorted(profiles.items())
            ]
            print(paint("   Память мозга:", DIM) + "\n" + "\n".join(paint(x, DIM) for x in lines))
    return {s: engine.seats[s].stack for s in stacks}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--strategy", default="heuristic")
    p.add_argument("--url", default=None, help="URL сервиса мозга (реальное время)")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--record", default=None, help="JSONL для записи истории (для replay_offline)")
    p.add_argument("--stacks", type=float, default=200.0)
    p.add_argument("--sb", type=float, default=1.0)
    p.add_argument("--bb", type=float, default=2.0)
    args = p.parse_args()

    brain = None
    if not args.url:
        brain = Brain(args.strategy, memory=InMemoryProfileStore(), seed=args.seed)

    where = f" через {args.url}" if args.url else " локально"
    print(paint(f"Pokafer — Вы против мозга «{args.strategy}»{where}. Блайнды {args.sb:g}/{args.bb:g}.", BOLD))
    print(HELP_TXT)
    stacks = {HERO: args.stacks, VILLAIN: args.stacks}
    button, hand_no = VILLAIN, 0
    while min(stacks.values()) > 0:
        hand_no += 1
        button = HERO if button == VILLAIN else VILLAIN
        header = f" РУКА #{hand_no} · кнопка: {NAMES[button]} · счёт {stacks[HERO]:g} : {stacks[VILLAIN]:g} "
        print("\n" + paint(f"═══════{header}═══════", BOLD))
        stacks = play_hand(args, brain, stacks, button, hand_no)
        if ask("Ещё руку? (Enter — да, q — выход): ").strip().lower() in ("q", "n", "н"):
            break
    print(f"Итог сессии: Вы {stacks[HERO]:g} — Мозг {stacks[VILLAIN]:g}")


if __name__ == "__main__":
    main()
