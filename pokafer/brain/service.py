"""HTTP-обёртка мозга (FastAPI): реальное время, JSON in/out.

Запуск:
    python scripts/serve_brain.py            # http://127.0.0.1:8000, docs на /docs
    uvicorn pokafer.brain.service:app        # альтернативно

Сервис stateless: состояние раздачи целиком передаётся в каждом запросе,
поэтому масштабируется горизонтально без сессий.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import pokafer
from pokafer.brain import strategies
from pokafer.brain.api import Brain
from pokafer.brain.memory import InMemoryProfileStore
from pokafer.core.schema import Snapshot


class DecideRequest(BaseModel):
    snapshot: Snapshot
    strategy: str | None = None
    seed: int | None = None


def create_app(default_strategy: str = "heuristic") -> FastAPI:
    app = FastAPI(title="Pokafer Brain", version=pokafer.__version__)
    brains: dict[str, Brain] = {}

    def _brain(strategy: str | None, seed: int | None) -> Brain:
        name = strategy or default_strategy
        key = f"{name}:{seed}"
        if key not in brains:
            brains[key] = Brain(strategy=name, memory=InMemoryProfileStore(), seed=seed)
        return brains[key]

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": pokafer.__version__}

    @app.get("/v1/strategies")
    def list_strategies() -> dict:
        return strategies.available()

    @app.get("/v1/schema")
    def schema() -> dict:
        return Snapshot.model_json_schema()

    @app.post("/v1/decide")
    def decide(req: DecideRequest) -> dict:
        try:
            brain = _brain(req.strategy, req.seed)
            brain.observe(req.snapshot)
            decision = brain.decide(req.snapshot)
        except KeyError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        out = decision.model_dump()
        out["decision"] = decision.key()
        return out

    return app


app = create_app()
