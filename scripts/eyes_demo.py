#!/usr/bin/env python3
"""Зрение в реальном времени на нашем столе: экран -> распознавание -> зеркало.

    pokafer eyes-demo [--url http://127.0.0.1:8010] [--headless] [--no-view]
                      [--fps 4] [--bots 5] [--seconds 0]

Открывает стол в управляемом браузере и в цикле читает кадры: карты и числа
по пресету сайта (только пиксели — как в бою). Герой пока не играет сам
(трекер и бой — следующие этапы): ходите за него вручную и смотрите в
зеркале, что видят глаза.

Зеркало — страница «что видят глаза»: http://127.0.0.1:8020/view/<сессия>.
Это отдельный процесс: на конвейер он не влияет, а без открытой страницы
конвейер и не публикует. --no-view — зеркало не запускать.

--bots настраивается DOM-кликами до начала цикла (это подготовка демо, а не
восприятие: в цикле глаз DOM не используется).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def viewer_is_up(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.6) as r:
            return json.loads(r.read()).get("service") == "pokafer-eyes-view"
    except (OSError, ValueError):
        return False


def start_viewer(port: int) -> bool:
    """Поднять зеркало отдельным процессом (отвязанным от этого терминала)."""
    subprocess.Popen([sys.executable, "-m", "pokafer.eyes.viewer", "--port", str(port)],
                     cwd=ROOT, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(0.25)
        if viewer_is_up(port):
            return True
    return False


def _wait_connected(browser, timeout: float = 10.0) -> None:
    """Стол подключился к серверу (строка статуса сменилась с «подключение…»)."""
    end = time.time() + timeout
    while time.time() < end:
        status = browser.execute("return document.getElementById('status').textContent;")
        if status and "подключение" not in status:
            return
        time.sleep(0.2)
    raise SystemExit("стол не подключился к серверу за 10 с")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:8010")
    p.add_argument("--headless", action="store_true",
                   help="без видимого окна: кадры CDP-скриншотом (для тестов)")
    p.add_argument("--no-view", action="store_true", help="не запускать зеркало и не публиковать")
    p.add_argument("--view-port", type=int, default=8020)
    p.add_argument("--fps", type=float, default=4.0)
    p.add_argument("--bots", type=int, default=5, help="сколько ботов посадить за стол (0 — не трогать)")
    p.add_argument("--seconds", type=float, default=0.0, help="0 — до Ctrl-C")
    args = p.parse_args()

    from pokafer.eyes.browser import TableBrowser
    from pokafer.eyes.cards import CardReader
    from pokafer.eyes.loop import watch
    from pokafer.eyes.mirror import MirrorPublisher
    from pokafer.eyes.numbers import NumberReader
    from pokafer.eyes.sites import preset_for_domain

    preset = preset_for_domain(args.url.split("//")[-1])
    card_reader = CardReader(preset)       # отказ на чужой калибровке/без шаблонов
    number_reader = NumberReader(preset)
    vw, vh = preset.viewport_ref or (1280, 720)

    session = uuid.uuid4().hex[:8]
    publisher = None if args.no_view else MirrorPublisher(session, args.url)
    try:
        browser = TableBrowser(f"{args.url.rstrip('/')}/?sid=eyes-{session}", viewport=(vw, vh),
                               headless=args.headless)
        try:
            if args.bots:
                _wait_connected(browser)   # клики до соединения с сервером пропадают
                browser.execute(
                    "for (let i = 0; i < arguments[0]; i++) document.getElementById('addBotBtn').click();",
                    args.bots - 1)
            if publisher is not None:
                if not viewer_is_up(args.view_port) and not start_viewer(args.view_port):
                    print("зеркало не поднялось — продолжаю без него", file=sys.stderr)
                    publisher.close()
                    publisher = None
            if publisher is not None:
                print(f"зеркало: http://127.0.0.1:{args.view_port}/view/{session}")
            print(f"смотрю стол {args.url} (пресет {preset.name}, калибровка {preset.calibration_id}); "
                  "Ctrl-C — остановить", flush=True)
            t0 = time.time()
            frames = watch(browser, card_reader, number_reader, publisher, fps=args.fps,
                           screen=not args.headless,
                           running=None if not args.seconds else
                           (lambda: time.time() - t0 < args.seconds))
            print(f"кадров обработано: {frames}")
        finally:
            browser.close()
    finally:
        if publisher is not None:
            publisher.close()


if __name__ == "__main__":
    main()
