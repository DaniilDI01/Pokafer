"""Подготовка нашего стола к прогону — сам стол и DOM страницы (не восприятие).

Цикл зрения и бой читают стол только пикселями. Всё, что нужно сделать со
столом ДО цикла: поднять его, если он не запущен; дождаться соединения
страницы с сервером; посадить ботов. Последние два идут через DOM страницы —
как руками. Это подготовка демо и боя, не глаза.
"""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from pokafer.eyes.browser import TableBrowser

ROOT = Path(__file__).resolve().parents[2]
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")   # здесь бой и демо сами поднимают наш стол

STATUS_JS = "return document.getElementById('status').textContent;"

# пересобрать стол: сколько ботов и с какой стратегией (кнопка «+ бот» делает
# то же, но по одному и со стратегией по умолчанию)
SEAT_BOTS_JS = """
botsCfg.splice(0, botsCfg.length,
  ...Array.from({ length: arguments[0] }, (_, i) => ({ name: "Мозг " + (i + 1), strategy: arguments[1] })));
sendConfigure();
"""


def wait_connected(browser: TableBrowser, timeout: float = 10.0) -> None:
    """Стол подключился к серверу (строка статуса сменилась с «подключение…»)."""
    end = time.time() + timeout
    while time.time() < end:
        status = browser.execute(STATUS_JS)
        if status and "подключение" not in status:
            return
        time.sleep(0.2)
    raise SystemExit(f"стол не подключился к серверу за {timeout:g} с")


def seat_bots(browser: TableBrowser, bots: int, strategy: str = "heuristic") -> None:
    """Посадить за стол ботов (1–5) с одной стратегией; сервер пересоберёт стол."""
    browser.execute(SEAT_BOTS_JS, bots, strategy)


# ---------- сам стол ----------

def answers(url: str) -> bool:
    """Отзывается ли наш веб-стол по адресу (списком стратегий)."""
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/v1/strategies", timeout=1.0) as r:
            return r.status == 200
    except (OSError, ValueError):
        return False


def ensure_table(url: str, timeout: float = 20.0) -> subprocess.Popen | None:
    """Наш стол слушает адрес — вернуть None; не запущен — поднять и вернуть его процесс.

    Поднимает только наш стол (localhost) и только если он не отвечает: стол,
    запущенный исследователем, не трогается. Чужой рум — не наше дело: о нём
    скажет браузер. Поднятый стол останавливается stop_table в конце прогона.
    """
    host = urllib.parse.urlsplit(url).hostname
    if host not in LOCAL_HOSTS or answers(url):
        return None
    port = urllib.parse.urlsplit(url).port or 80
    proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "serve_table.py"), "--port", str(port)],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"стол не запущен — поднимаю наш веб-стол на {url}", flush=True)
    end = time.time() + timeout
    while time.time() < end and proc.poll() is None:
        if answers(url):
            return proc
        time.sleep(0.2)
    stop_table(proc)
    raise SystemExit(f"стол не поднялся на {url}: запустите его в другом терминале — "
                     f"pokafer table --port {port}")


def stop_table(proc: subprocess.Popen | None) -> None:
    """Остановить стол, который подняли сами (None — стол чужой, не трогаем)."""
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
