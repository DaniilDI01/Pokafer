"""WebSocket-тесты веб-стола: подключение, ходы, мультиботы, автосдача."""
import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from pokafer.web.table_server import create_table_app  # noqa: E402


def _recv_until(ws, type_: str, limit: int = 300):
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == type_:
            return msg
    raise AssertionError(f"не дождались сообщения {type_!r}")


def _recv_view(ws, *, fresh=False, finished=False, limit: int = 300):
    """Ждём view: любой; fresh=True — только незавершённый (новая рука);
    finished=True — только завершённый."""
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] != "view":
            continue
        if fresh and msg["hand_over"]:
            continue
        if finished and not msg["hand_over"]:
            continue
        return msg
    raise AssertionError("не дождались подходящего view")


def _play_hero_passively(ws, max_rounds: int = 300) -> dict:
    """Герой играет только чек/колл. Возвращает result (идёт до финального view)."""
    for _ in range(max_rounds):
        msg = ws.receive_json()
        if msg["type"] == "result":
            return msg
        if msg["type"] != "view" or msg["hand_over"]:
            continue
        if msg["to_act"] == 1:
            legal = msg["legal"]
            act = "call" if legal["call"] is not None else "check"
            ws.send_json({"type": "action", "action": act})
    raise AssertionError("раздача не доигралась")


def test_connect_and_first_view():
    with TestClient(create_table_app("heuristic", bot_think_sec=0.0)) as client:
        with client.websocket_connect("/ws/table") as ws:
            view = _recv_view(ws)
            assert view["hero"]["cards"] and len(view["hero"]["cards"]) == 2
            assert len(view["bots"]) == 1
            assert view["street"]


def test_hand_completes_and_new_hand():
    with TestClient(create_table_app("heuristic", bot_think_sec=0.0)) as client:
        with client.websocket_connect("/ws/table") as ws:
            result = _play_hero_passively(ws)
            assert result["kind"] in ("showdown", "fold_win")
            ws.send_json({"type": "new_hand"})
            view2 = _recv_view(ws, fresh=True)
            assert view2["hand_no"] == 2
            assert view2["hand_over"] is False


def test_illegal_action_returns_error_not_crash():
    with TestClient(create_table_app("heuristic", bot_think_sec=0.0)) as client:
        with client.websocket_connect("/ws/table") as ws:
            _recv_view(ws)
            ws.send_json({"type": "action", "action": "raise", "amount": 999999})
            err = _recv_until(ws, "error")
            assert err["detail"]


def test_auto_next_hand():
    app = create_table_app("heuristic", bot_think_sec=0.0, auto_next_sec=0.3)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/table") as ws:
            result = _play_hero_passively(ws)
            assert result["type"] == "result"
            # ничего не шлём: следующая рука должна прийти сама
            view2 = _recv_view(ws, fresh=True)
            assert view2["hand_no"] == 2


def test_configure_multi_bots_and_play():
    app = create_table_app("heuristic", bot_think_sec=0.0)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/table") as ws:
            # пересобираем стол сразу; старую раздачу не играем
            ws.send_json({"type": "configure", "bots": [
                {"name": "Микро", "strategy": "micro"},
                {"name": "Рыба", "strategy": "fish"},
            ]})
            for _ in range(50):
                m = ws.receive_json()
                if m["type"] == "log" and "пересобран" in m["text"]:
                    break

            result = _play_hero_passively(ws)
            assert result["kind"] in ("showdown", "fold_win")

            final = _recv_view(ws, finished=True)  # стеки после раздачи
            names = {b["name"] for b in final["bots"]}
            assert names == {"Микро", "Рыба"}
            strategies = {b["strategy"] for b in final["bots"]}
            assert strategies == {"micro", "fish"}
            assert final["hero"]["name"] == "Вы"
            if result["kind"] == "showdown":
                # карты шоудауна не прячутся до следующей руки
                assert any(isinstance(b["cards"], list) and b["cards"]
                           for b in final["bots"])
