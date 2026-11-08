"""WebSocket-сервер веб-стола: человек (место 1) против нескольких ботов.

Боты настраиваются на лету: {"type": "configure", "bots": [{"name": "Мозг 1",
"strategy": "micro"}, ...]} (до 5 ботов, у каждого своя стратегия).

Протокол (JSON), сервер -> клиент:
  view    — вид стола с перспективы героя (карты ботов скрыты до шоудауна)
  bot     — ход конкретного бота с его распределением вероятностей
  result  — итог раздачи (банки/сайд-поты, карты на шоудауне)
  hint    — подсказка мозга по текущей ситуации героя
  log/error — служебные сообщения
клиент -> сервер:
  action    {action: fold|check|call|bet|raise, amount?: float}
  new_hand  — сдать следующую руку немедленно (иначе автосдача по таймеру)
  hint      — попросить подсказку
  strategy  {name} — сменить стратегию единственного бота (совместимость)
  configure {bots: [...]} — пересобрать стол: список ботов и их стратегии
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pokafer.adapters.replay_file import ReplayFile
from pokafer.brain.api import Brain
from pokafer.brain.strategies import available as strategies_available
from pokafer.core.engine import HandEngine

HERO = 1
HERO_NAME = "Вы"
START_STACK = 200.0
MAX_BOTS = 5
STREET_RU = {
    "preflop": "префлоп", "flop": "флоп", "turn": "терн",
    "river": "ривер", "showdown": "шоудаун",
}

STATIC_DIR = Path(__file__).parent / "static"


def _ru_action(decision) -> str:
    verb, _, amt = decision.key().partition(":")
    return {
        "fold": "фолд", "check": "чек",
        "call": f"колл {amt}" if amt else "колл",
        "bet": f"ставка {amt}" if amt else "ставка",
        "raise": f"рейз до {amt}" if amt else "рейз",
    }.get(verb, decision.key())


class TableSession:
    """Состояние подключения: места (герой + боты), мозги ботов, стеки."""

    def __init__(self, strategy: str = "heuristic", seed: int | None = None,
                 record_path: str | None = None,
                 bots: list[dict] | None = None):
        self.record_path = record_path
        self.hand_no = 0
        self.stacks: dict[int, float] = {}
        self.names: dict[int, str] = {}
        self.bot_strategy: dict[int, str] = {}  # seat -> стратегия
        self.button = 0
        self.set_bots(bots if bots is not None else [
            {"name": "Мозг", "strategy": strategy}
        ])
        self._seed = seed
        self._reveal: dict[int, list[str]] = {}  # карты шоудауна — не прячем до след. руки
        self._start_hand()

    # ---------- конфигурация ----------

    def set_bots(self, bots: list[dict]) -> None:
        if not 1 <= len(bots) <= MAX_BOTS:
            raise ValueError(f"ботов должно быть от 1 до {MAX_BOTS}")
        known = strategies_available()
        names_seen: set[str] = set()
        self.names = {HERO: HERO_NAME}
        self.bot_strategy = {}
        for i, b in enumerate(bots, start=2):
            strategy = b.get("strategy", "heuristic")
            if strategy not in known:
                raise KeyError(f"нет стратегии {strategy!r}")
            name = str(b.get("name") or f"Мозг {i - 1}").strip() or f"Мозг {i - 1}"
            while name in names_seen:
                name += "*"
            names_seen.add(name)
            self.names[i] = name
            self.bot_strategy[i] = strategy
        self.stacks = {s: START_STACK for s in self.names}
        self.button = 0
        self._reveal = {}

    def _start_hand(self) -> None:
        if min(self.stacks.values()) <= 0:
            self.stacks = {s: START_STACK for s in self.names}
        self.hand_no += 1
        self._reveal = {}
        seats_order = sorted(self.names)
        if self.button in self.names:
            self.button = seats_order[(seats_order.index(self.button) + 1) % len(seats_order)]
        else:
            self.button = seats_order[0]
        self.engine = HandEngine(
            seat_stacks=dict(self.stacks),
            names=self.names,
            button_seat=self.button,
            hand_id=f"web-{self.hand_no:04d}",
            table_id="web-table",
        )
        self.brains = {
            seat: Brain(strategy, seed=self._seed)
            for seat, strategy in self.bot_strategy.items()
        }

    # ---------- вид для клиента ----------

    def view(self) -> dict:
        e = self.engine
        snap = e.snapshot_for(HERO)
        me = next(s for s in snap.seats if s.seat == HERO)
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
            "hero": {
                "seat": HERO, "name": HERO_NAME, "stack": me.stack,
                "bet": me.bet_street, "invested": me.invested,
                "folded": me.folded, "all_in": me.all_in,
                "cards": me.hole_cards,
                "is_button": self.button == HERO,
            },
            "bots": [
                {
                    "seat": s.seat, "name": self.names[s.seat], "stack": s.stack,
                    "bet": s.bet_street, "invested": s.invested,
                    "folded": s.folded, "all_in": s.all_in,
                    # до шоудауна — закрыты; после итога — раскрыты и не прячутся
                    "cards": s.hole_cards or (
                        self._reveal.get(s.seat) if e.hand_over else None
                    ),
                    "strategy": self.bot_strategy.get(s.seat),
                    "is_button": self.button == s.seat,
                }
                for s in snap.seats if s.seat != HERO
            ],
            "to_act": None if e.hand_over else e.actor_seat,
            "legal": legal,
            "hand_over": e.hand_over,
            "stackline": " · ".join(
                f"{self.names[s]} {self.stacks.get(s, 0):g}"
                for s in sorted(self.stacks)
            ),
        }

    # ---------- ходы ----------

    def bot_step(self) -> dict | None:
        """Один ход бота, если его очередь."""
        e = self.engine
        if e.hand_over or e.actor_seat == HERO:
            return None
        seat = e.actor_seat
        brain = self.brains[seat]
        snap = e.snapshot_for(seat)
        d = brain.decide(snap)
        e.apply(d.action, d.amount_to)
        if self.record_path:
            ReplayFile.append(self.record_path, snap, d.model_dump())
        return {
            "type": "bot",
            "seat": seat,
            "name": self.names[seat],
            "decision": d.key(),
            "ru": _ru_action(d),
            "dist": {k: round(v, 3) for k, v in d.distribution.items()},
            "reasoning": d.reasoning,
        }

    def finish_hand(self) -> dict | None:
        e = self.engine
        if not e.hand_over:
            return None
        res = e.result or {}
        self.stacks = {s: e.seats[s].stack for s in self.names}
        self._reveal = {s: c for s, c in res.get("hole_cards", {}).items()}
        name_of = self.names
        return {
            "type": "result",
            "kind": res.get("type"),
            "winners": [name_of[w] for w in res.get("winners", [])],
            "pot": res.get("pot"),
            "amount": res.get("amount"),
            "winnings": {name_of[s]: a for s, a in res.get("winnings", {}).items()},
            "pots": [
                {"amount": p["amount"], "winners": [name_of[w] for w in p["winners"]]}
                for p in res.get("pots", [])
            ],
            "scores": {name_of[s]: n for s, n in res.get("scores_ru", res.get("scores", {})).items()},
            "hole_cards": {name_of[s]: c for s, c in res.get("hole_cards", {}).items()},
        }


def create_table_app(default_strategy: str = "heuristic",
                     bot_think_sec: float = 0.5,
                     auto_next_sec: float = 5.0) -> FastAPI:
    app = FastAPI(title="Pokafer Web Table")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/v1/strategies")
    def list_strategies():
        return strategies_available()

    @app.websocket("/ws/table")
    async def table(ws: WebSocket, strategy: str = default_strategy, seed: int | None = None):
        await ws.accept()
        session = TableSession(strategy=strategy, seed=seed)
        auto_task: asyncio.Task | None = None

        def cancel_auto() -> None:
            nonlocal auto_task
            if auto_task is not None:
                auto_task.cancel()
                auto_task = None

        async def schedule_auto() -> None:
            nonlocal auto_task
            cancel_auto()

            async def _auto() -> None:
                await asyncio.sleep(auto_next_sec)
                if session.engine.hand_over:
                    session._start_hand()
                    await ws.send_json(session.view())
                    await advance_bot()

            auto_task = asyncio.create_task(_auto())

        async def advance_bot() -> None:
            """Доигрывает ходы ботов и закрывает раздачу (с автосдачей)."""
            while not session.engine.hand_over and session.engine.actor_seat != HERO:
                await asyncio.sleep(bot_think_sec)
                event = session.bot_step()
                if event is None:
                    break
                await ws.send_json(event)
                await ws.send_json(session.view())
            if session.engine.hand_over:
                await ws.send_json(session.finish_hand())
                await ws.send_json(session.view())
                await schedule_auto()

        try:
            await ws.send_json(session.view())
            await advance_bot()
            while True:
                msg = await ws.receive_json()
                mtype = msg.get("type")
                if mtype == "action":
                    cancel_auto()
                    try:
                        session.engine.apply(msg["action"], msg.get("amount"))
                    except (ValueError, KeyError, RuntimeError) as e:
                        await ws.send_json({"type": "error", "detail": str(e)})
                        continue
                    await ws.send_json(session.view())
                    await advance_bot()
                elif mtype == "new_hand":
                    cancel_auto()
                    if session.engine.hand_over:
                        session._start_hand()
                    await ws.send_json(session.view())
                    await advance_bot()
                elif mtype == "configure":
                    cancel_auto()
                    try:
                        session.set_bots(msg.get("bots", []))
                        session._start_hand()
                        await ws.send_json({"type": "log", "text": "стол пересобран"})
                    except (ValueError, KeyError) as e:
                        await ws.send_json({"type": "error", "detail": str(e)})
                        continue
                    await ws.send_json(session.view())
                    await advance_bot()
                elif mtype == "hint":
                    snap = session.engine.snapshot_for(HERO)
                    d = session.brains[next(iter(session.brains))].decide(snap) \
                        if session.brains else None
                    if d is not None:
                        await ws.send_json({
                            "type": "hint",
                            "ru": _ru_action(d),
                            "dist": {k: round(v, 3) for k, v in d.distribution.items()},
                            "reasoning": d.reasoning,
                        })
                elif mtype == "strategy":
                    # совместимость: смена стратегии единственного бота
                    try:
                        bots = [{"name": session.names[s], "strategy": msg["name"]}
                                for s in session.bot_strategy]
                        session.set_bots(bots)
                        await ws.send_json({"type": "log", "text": f"стратегия: {msg['name']}"})
                    except KeyError as e:
                        await ws.send_json({"type": "error", "detail": str(e)})
                        continue
                    await ws.send_json(session.view())
        except WebSocketDisconnect:
            cancel_auto()
            return
        finally:
            cancel_auto()

    return app
