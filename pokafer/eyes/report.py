"""Строки терминала: что сказали трекер и пилот (eyes-demo, бой, приёмка боя).

Тексты — в одном месте: глаза, трекер и пилот считают, здесь их слова
превращаются в строки для терминала.
"""
from __future__ import annotations

from pokafer.core.schema import ActionRecord, Decision
from pokafer.eyes.pilot import Note
from pokafer.eyes.tracker import HERO, Event, Tracker

STREET_RU = {"preflop": "префлоп", "flop": "флоп", "turn": "терн", "river": "ривер", "showdown": "вскрытие"}


def action_ru(kind: str, to: float | None = None, amount: float = 0.0) -> str:
    """Действие словами: «рейз до 6», «колл 4», «блайнд 2», «чек», «фолд», «ставка 6»."""
    if kind == "post":
        return f"блайнд {amount:g}"
    if kind in ("bet", "raise"):
        return f"{'ставка' if kind == 'bet' else 'рейз'} до {to:g}"
    if kind == "call":
        return f"колл {amount:g}"
    return {"check": "чек", "fold": "фолд"}[kind]


def record_ru(a: ActionRecord) -> str:
    """Действие из истории словами, с именем: «место 3: рейз до 6», «герой: колл 4»."""
    who = "герой" if a.seat == HERO else f"место {a.seat}"
    return f"{who}: {action_ru(a.type, a.amount_to, a.amount)}"


def decision_ru(d: Decision, call: float = 0.0) -> str:
    """Решение героя словами — без имени: «колл 4»; call — сколько докупить (сумма с кнопки того же состояния)."""
    return action_ru("call", amount=call) if d.action == "call" else action_ru(d.action, d.amount_to)


def describe(tracker: Tracker, event: Event) -> str:
    """Строка терминала о событии трекера."""
    hand = tracker.hand
    if event.kind == "hand_start":
        return f"рука {hand.no}: герой {' '.join(hand.hero_cards)}"
    if event.kind == "actions":
        return "  " + "; ".join(record_ru(a) for a in event.actions)
    if event.kind == "street":
        return f"  {STREET_RU[tracker.snapshot.street]}: {' '.join(hand.board)}"
    if event.kind == "hero_turn":
        call = tracker.level.call
        what = f"колл {call:g}" if call else "чек"
        return f"  ход героя: {what} (банк {hand.pot:g}, действий {len(hand.actions)})"
    if event.kind == "hand_over":
        return f"  рука окончена: действий {len(hand.actions)}" + (f", пропусков {hand.gaps}" if hand.gaps else "")
    if event.kind == "gap":
        return "  пропуск: ходы между кадрами не восстановились однозначно — история улицы неполна"
    return "  рассинхронизация: банк не сходится с деньгами на столе"


def summary(pilot) -> list[str]:
    """Итог боя строками: руки, исход героя, решения, исполнение, повторы."""
    s = pilot.stats
    kinds = ", ".join(f"{name} {s[f'решение: {kind}']}" for kind, name in
                      (("fold", "фолд"), ("check", "чек"), ("call", "колл"), ("bet", "ставка"), ("raise", "рейз")))
    return [
        f"рук сыграно: {pilot.hands}; исход героя по стекам: {pilot.won:+g}",
        f"решений: {s['решения']} ({kinds}); мозг думал в среднем "
        f"{pilot.thinking / max(s['решения'], 1):.0f} мс",
        f"кликов исполнено как решено: {s['исполнено как решено']}; иначе: {s['исполнено иначе']}; "
        f"не проверено (пропуск в истории): {s['исполнение не проверено']}",
        f"повторов кликов: {s['повторы']} (из них «следующая рука»: {s['повторы: следующая рука']}); "
        f"решений по неполной истории: {s['решения по неполной истории']}; "
        f"рейз без отвечающего сыгран коллом: {s['рейз без отвечающего — колл']}",
        f"трекер: пропусков интервалов {pilot.tracker.stats['интервал не восстановлен']}, "
        f"недостоверных кадров {sum(v for k, v in pilot.tracker.stats.items() if k.startswith('кадр недостоверный'))}",
    ]


def describe_note(note: Note, call: float = 0.0) -> str:
    """Строка терминала о ходе пилота."""
    d = note.decision
    if note.kind == "decision":
        tries = f", попытка {note.tries}" if note.tries > 1 else ""
        return f"  герой: {decision_ru(d, call)} ← {d.strategy}, {d.elapsed_ms:.0f} мс{tries}: {d.reasoning}"
    if note.kind == "retry":
        return f"  клик не подействовал — повтор {note.tries}: {decision_ru(d, call)}"
    if note.kind == "mismatch":
        seen = record_ru(note.got) if note.got is not None else "действия героя нет"
        return f"  КЛИК ИСПОЛНИЛСЯ ИНАЧЕ: решено {decision_ru(d, call)}, в истории {seen}"
    if note.kind == "unverified":
        return "  исполнение не проверено: история руки восстановлена с пропуском"
    return f"  «следующая рука» не подействовала — повтор {note.tries}"
