"""Зеркало глаз — локальная страница «что видят глаза», отдельный процесс.

    pokafer eyes-view [--port 8020]

Конвейер глаз (pokafer eyes-demo, позже — бой) публикует состояние в
разделяемую память (pokafer/eyes/mirror.py); этот процесс читает её и раздаёт
браузеру: кадр JPEG + JSON с рамками и прочтениями. Страница рисует всё на
одном холсте и перерисовывается только на новом кадре.

Изоляция: зеркало — другой процесс. Пока страницу никто не открыл, конвейер
не публикует вовсе; падение зеркала или страницы конвейер не замечает.
"""
from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from pokafer.eyes import mirror

PAGE = Path(__file__).with_name("viewer_page.html")
SESSION_NAME = re.compile(r"^[\w-]{1,64}$")
POLL_SEC = 0.08          # частота опроса зеркала (≈ 12 кадров/с максимум)


def create_viewer_app() -> FastAPI:
    app = FastAPI(title="Pokafer — зеркало глаз", docs_url=None, redoc_url=None)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "service": "pokafer-eyes-view"}

    @app.get("/api/sessions")
    def list_sessions() -> list[dict]:
        return mirror.sessions()

    def serve_page() -> HTMLResponse:
        # страница читается с диска на каждый запрос: правки зеркала видны после F5,
        # без перезапуска процесса
        return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    @app.get("/")
    def index() -> HTMLResponse:
        return serve_page()

    @app.get("/view/{session}")
    def view(session: str) -> HTMLResponse:
        if not SESSION_NAME.match(session):
            return HTMLResponse("недопустимое имя сессии", status_code=400)
        return serve_page()

    @app.websocket("/ws/{session}")
    async def stream(ws: WebSocket, session: str) -> None:
        if not SESSION_NAME.match(session):
            await ws.close(code=1008)
            return
        await ws.accept()
        sub: mirror.MirrorSubscriber | None = None
        idle = 0
        try:
            while True:
                if sub is None:  # конвейер мог ещё не стартовать — пробуем снова
                    try:
                        sub = mirror.MirrorSubscriber(session)
                        sub.subscribe(True)
                    except FileNotFoundError:
                        idle += 1
                        if idle == 1:
                            await ws.send_json({"ok": False,
                                                "text": "конвейер глаз не запущен для этой сессии — жду…"})
                        await asyncio.sleep(1.0)
                        continue
                payload = sub.poll()
                if payload is not None:
                    await ws.send_bytes(payload)
                    idle = 0
                    continue
                idle += 1
                if idle % 25 == 0:  # раз в ~2 секунды — подписка и свежесть
                    age = sub.age
                    await ws.send_json({"ok": age < 3, "age": age if age != float("inf") else None,
                                        "text": "нет кадров: конвейер не публикует" if age >= 3
                                                else "связь с конвейером есть"})
                await asyncio.sleep(POLL_SEC)
        except WebSocketDisconnect:
            pass
        finally:
            if sub is not None:
                sub.subscribe(False)
                sub.close()

    return app


def serve(host: str = "127.0.0.1", port: int = 8020) -> None:
    import uvicorn

    uvicorn.run(create_viewer_app(), host=host, port=port, log_level="warning")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8020)
    args = p.parse_args()
    print(f"зеркало глаз: http://{args.host}:{args.port}/   (Ctrl-C — остановить)", flush=True)
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
