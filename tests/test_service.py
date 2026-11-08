import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from pokafer.brain.service import create_app  # noqa: E402
from pokafer.core.engine import HandEngine  # noqa: E402


def _client():
    return TestClient(create_app("heuristic"))


def _snapshot_payload(seed: int = 5):
    e = HandEngine(seat_stacks={1: 200.0, 2: 200.0}, names={1: "a", 2: "b"}, button_seat=1, seed=seed)
    return e.snapshot_for(e.actor_seat).model_dump(), {la.key for la in e.legal()}


def test_health():
    r = _client().get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_strategies_listed():
    r = _client().get("/v1/strategies")
    assert "heuristic" in r.json()
    assert "random" in r.json()


def test_decide_endpoint():
    payload, legal = _snapshot_payload()
    r = _client().post("/v1/decide", json={"snapshot": payload, "seed": 42})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] in legal
    assert abs(sum(body["distribution"].values()) - 1.0) < 1e-3
    assert body["elapsed_ms"] >= 0


def test_decide_bad_strategy():
    payload, _ = _snapshot_payload()
    r = _client().post("/v1/decide", json={"snapshot": payload, "strategy": "nope"})
    assert r.status_code == 400


def test_schema_endpoint():
    r = _client().get("/v1/schema")
    assert "properties" in r.json()
