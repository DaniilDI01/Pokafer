"""WebSocket-тесты веб-стола: подключение, ход героя, ход мозга, итог, ошибки."""
import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from pokafer.web.table_server import create_table_app  # noqa: E402


def _recv_until(ws, type_: str, limit: int = 200):
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == type_:
            return msg
    raise AssertionError(f"не дождались сообщения {type_!r}")


def test_connect_and_first_view():
    with TestClient(create_table_app("heuristic", bot_think_sec=0.0)) as client:
        with client.websocket_connect("/ws/table") as ws:
            view = _recv_until(ws, "view")
            assert view["hero"]["cards"] and len(view["hero"]["cards"]) == 2
            assert view["street"]
            assert view["to_act"] is not None or view["hand_over"]


def test_hand_completes_and_new_hand():
    """Герой играет только чек/колл: раздача обязана дойти до итога."""
    with TestClient(create_table_app("heuristic", bot_think_sec=0.0)) as client:
        with client.websocket_connect("/ws/table") as ws:
            for _ in range(80):
                view = _recv_until(ws, "view")
                if view["hand_over"]:
                    break
                if view["to_act"] == "hero":
                    legal = view["legal"]
                    act = "call" if legal["call"] is not None else "check"
                    ws.send_json({"type": "action", "action": act})
            result = _recv_until(ws, "result")
            assert result["kind"] in ("showdown", "fold_win")

            ws.send_json({"type": "new_hand"})
            # после итога сервер шлёт финальный view старой руки (обновление стеков) —
            # ждём именно view новой руки
            view2 = None
            for _ in range(200):
                msg = ws.receive_json()
                if msg["type"] == "view" and not msg["hand_over"]:
                    view2 = msg
                    break
            assert view2 is not None, "не дождались view новой руки"
            assert view2["hand_no"] == 2


def test_illegal_action_returns_error_not_crash():
    with TestClient(create_table_app("heuristic", bot_think_sec=0.0)) as client:
        with client.websocket_connect("/ws/table") as ws:
            _recv_until(ws, "view")
            ws.send_json({"type": "action", "action": "raise", "amount": 999999})
            err = _recv_until(ws, "error")
            assert err["detail"]
