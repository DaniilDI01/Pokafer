"""WebSocket-сервер веб-стола: человек (место 1) против мозга (место 2).

Протокол (JSON):
  сервер -> клиент:
    view    — вид стола с перспективы героя (карты бота скрыты до шоудауна)
    bot     — ход мозга с распределением (демонстрация смешанной стратегии)
    result  — итог раздачи (карты раскрываются на шоудауне)
    hint    — подсказка мозга по текущей ситуации
    error   — ошибка (напр., нелегальное действие)
  клиент -> сервер:
    action    {action: fold|check|call|bet|raise, amount?: float}
    new_hand  — сдать следующую руку
    hint      — попросить подсказку
    strategy  {name} — сменить стратегию мозга со следующей руки
"""
from __future__ import annotations

import asyncio
import random
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pokafer.adapters.replay_file import ReplayFile
from pokafer.brain.api import Brain
from pokafer.brain.memory import InMemoryProfileStore
from pokafer.brain.strategies import available as strategies_available
from pokafer.core.cards import evaluate7, hand_name_ru, parse_card
from pokafer.core.engine import HandEngine

HERO, VILLAIN = 1, 2
NAMES = {HERO: "Вы", VILLAIN: "Мозг"}
START_STACK = 200.0
BOT_THINK_SEC = 0.6  # пауза для живости

STATIC_DIR = Path(__file__).parent / "static"
STREET_RU = {
    "preflop": "префлоп", "flop": "флоп", "turn": "терн",
    "river": "ривер", "showdown": "шоудаун",
}


def _ru_action(decision) -> str:
    verb, _, amt = decision.key().partition(":")
    return {
        "fold": "фолд", "check": "чек",
        "call": f"колл {amt}" if amt else "колл",
        "bet": f"ставка {amt}" if amt else "ставка",
        "raise": f"рейз до {amt}" if amt else "рейз",
    }.get(verb, decision.key())


class TableSession:
    """Состояние одного подключения: движок, мозг, стеки между руками."""

    def __init__(self, strategy: str = "heuristic", seed: int | None = None,
                 record_path: str | None = None):
        self.strategy_name = strategy
        self.brain = Brain(strategy, memory=InMemoryProfileStore(), seed=seed)
        self.record_path = record_path
        self.stacks = {HERO: START_STACK, VILLAIN: START_STACK}
        self.button = HERO
        self.hand_no = 0
        self._start_hand()

    def _start_hand(self) -> None:
        if min(self.stacks.values()) <= 0:  # кто-то bust — новая сессия стеков
            self.stacks = {HERO: START_STACK, VILLAIN: START_STACK}
        self.hand_no += 1
        self.button = HERO if self.button == VILLAIN else VILLAIN
        self.engine = HandEngine(
            seat_stacks=dict(self.stacks),
            names=NAMES,
            button_seat=self.button,
            hand_id=f"web-{self.hand_no:04d}",
            table_id="web-table",
        )

    def set_strategy(self, name: str) -> None:
        if name not in strategies_available():
            raise KeyError(f"нет стратегии {name!r}")
        self.strategy_name = name
        self.brain = Brain(name, memory=InMemoryProfileStore())

    # ---------- вид для клиента ----------

    def view(self) -> dict:
        e = self.engine
        snap = e.snapshot_for(HERO)
        me = next(s for s in snap.seats if s.seat == HERO)
        opp = next(s for s in snap.seats if s.seat == VILLAIN)
        legal: dict = {"fold": False, "check": False, "call": None, "raise": None}
        if not e.hand_over and e.actor_seat == HERO:
            for la in e.legal(HERO):
                if la.action == "fold":
                    legal["fold"] = True
                elif la.action == "check":
                    legal["check"] = True
                elif la.action == "call":
                    legal["call"] = round(la.amount_to - me.bet_street, 2)
                elif la.action in ("bet", "raise"):
                    if legal["raise"] is None:
                        legal["raise"] = {"min": la.amount_to}
                    legal["raise"]["max"] = la.amount_to
        return {
            "type": "view",
            "hand_no": self.hand_no,
            "button_seat": self.button,
            "street": STREET_RU[snap.street],
            "board": snap.board,
            "pot": snap.pot,
            "hero": self._seat_view(me),
            "villain": self._seat_view(opp),
            "to_act": None if e.hand_over else ("hero" if e.actor_seat == HERO else "villain"),
            "legal": legal,
            "hand_over": e.hand_over,
            "stackline": f"{self.stacks[HERO]:g} : {self.stacks[VILLAIN]:g}",
        }

    @staticmethod
    def _seat_view(s) -> dict:
        return {
            "name": NAMES[s.seat],
            "stack": s.stack,
            "bet": s.bet_street,
            "invested": s.invested,
            "folded": s.folded,
            "all_in": s.all_in,
            "cards": s.hole_cards,
            "is_hero": s.is_hero,
        }

    def hero_combo(self) -> str | None:
        snap = self.engine.snapshot_for(HERO)
        me = next(s for s in snap.seats if s.seat == HERO)
        if not me.hole_cards or not snap.board:
            return None
        cards = [parse_card(c) for c in list(me.hole_cards) + list(snap.board)]
        return hand_name_ru(evaluate7(cards))

    # ---------- ходы ----------

    def apply_hero(self, action: str, amount: float | None) -> None:
        self.engine.apply(action, amount)

    def bot_step(self) -> dict | None:
        """Один ход мозга (если его очередь). Возвращает событие для клиента."""
        e = self.engine
        if e.hand_over or e.actor_seat != VILLAIN:
            return None
        snap = e.snapshot_for(VILLAIN)
        d = self.brain.decide(snap)
        e.apply(d.action, d.amount_to)
        if self.record_path:
            ReplayFile.append(self.record_path, snap, d.model_dump())
        return {
            "type": "bot",
            "decision": d.key(),
            "ru": _ru_action(d),
            "dist": {k: round(v, 3) for k, v in d.distribution.items()},
            "reasoning": d.reasoning,
        }

    def finish_hand(self) -> dict | None:
        """Фиксирует итог руки (стеки), но НЕ сдаёт следующую — её попросит клиент."""
        e = self.engine
        if not e.hand_over:
            return None
        res = e.result or {}
        self.stacks = {s: e.seats[s].stack for s in (HERO, VILLAIN)}
        return {
            "type": "result",
            "kind": res.get("type"),
            "winners": [NAMES[w] for w in res.get("winners", [])],
            "pot": res.get("pot"),
            "amount": res.get("amount"),
            "scores": res.get("scores_ru", res.get("scores", {})),
            "hole_cards": {NAMES[k]: v for k, v in res.get("hole_cards", {}).items()},
        }


def create_table_app(default_strategy: str = "heuristic", bot_think_sec: float = BOT_THINK_SEC) -> FastAPI:
    app = FastAPI(title="Pokafer Web Table")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/v1/strategies")
    def list_strategies():
        return strategies_available()

    async def advance_bot(session: TableSession, ws: WebSocket) -> None:
        """Доигрывает ходы мозга (с паузой для живости) и закрывает раздачу."""
        while not session.engine.hand_over and session.engine.actor_seat == VILLAIN:
            await asyncio.sleep(bot_think_sec)
            event = session.bot_step()
            if event is None:
                break
            await ws.send_json(event)
            await ws.send_json(session.view())
        if session.engine.hand_over:
            await ws.send_json(session.finish_hand())
            await ws.send_json(session.view())  # свежие стеки и кнопка «следующая рука»

    @app.websocket("/ws/table")
    async def table(ws: WebSocket, strategy: str = default_strategy, seed: int | None = None):
        await ws.accept()
        session = TableSession(strategy=strategy, seed=seed)
        await ws.send_json(session.view())
        try:
            await advance_bot(session, ws)  # бот может ходить первым (SB на своей кнопке)
            while True:
                msg = await ws.receive_json()
                mtype = msg.get("type")
                if mtype == "action":
                    try:
                        session.apply_hero(msg["action"], msg.get("amount"))
                    except (ValueError, KeyError, RuntimeError) as e:
                        await ws.send_json({"type": "error", "detail": str(e)})
                        continue
                    await ws.send_json(session.view())
                elif mtype == "new_hand":
                    session._start_hand()
                    await ws.send_json(session.view())
                elif mtype == "hint":
                    snap = session.engine.snapshot_for(HERO)
                    d = session.brain.decide(snap)
                    await ws.send_json({
                        "type": "hint",
                        "ru": _ru_action(d),
                        "dist": {k: round(v, 3) for k, v in d.distribution.items()},
                        "reasoning": d.reasoning,
                    })
                    continue
                elif mtype == "strategy":
                    try:
                        session.set_strategy(msg["name"])
                        await ws.send_json({"type": "log", "text": f"стратегия: {msg['name']}"})
                    except KeyError as e:
                        await ws.send_json({"type": "error", "detail": str(e)})
                    continue
                else:
                    continue
                await advance_bot(session, ws)
        except WebSocketDisconnect:
            return

    return app
