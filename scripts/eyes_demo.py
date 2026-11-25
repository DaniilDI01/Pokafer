#!/usr/bin/env python3
"""Зрение в реальном времени на нашем столе: кадр страницы -> распознавание -> зеркало.

    pokafer eyes-demo [--url http://127.0.0.1:8010] [--headless] [--no-view] [--no-open]
                      [--fps 4] [--bots 5] [--seconds 0]

Открывает стол в управляемом браузере и в цикле читает кадры: карты и числа
по пресету сайта (только пиксели — как в бою). Герой пока не играет сам
(трекер и бой — следующие этапы): ходите за него вручную и смотрите в
зеркале, что видят глаза.

Окно браузера свободно: его можно таскать и перекрывать другими — кадры
отдаёт сам браузер, а не монитор. Свернуть его нельзя (скрытую страницу
браузер не рисует): свёрнутое окно глаза разворачивают сами и пишут об этом;
чтобы окна не было вовсе — --headless (но тогда за героя руками не походить).

Зеркало — страница «что видят глаза»: http://127.0.0.1:8020/view/<сессия>.
Открывается сама в браузере по умолчанию и может лежать как угодно, хоть
поверх стола: на конвейер не влияет — зеркало отдельный процесс, а без
открытой страницы конвейер и не публикует. --no-view — зеркало не запускать;
--no-open — страницу зеркала не открывать.

--bots настраивается DOM-кликами до начала цикла (это подготовка демо, а не
восприятие: в цикле глаз DOM не используется).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
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


def open_in_browser(url: str) -> None:
    """Открыть зеркало в браузере по умолчанию — в фоне и без последствий для конвейера."""
    def open() -> None:
        try:
            import webbrowser
            opened = webbrowser.open(url)
        except Exception as e:      # отладочное окно не должно мешать конвейеру
            opened = False
            print(f"зеркало не открылось само ({e})", file=sys.stderr, flush=True)
        if not opened:
            print(f"откройте зеркало сами: {url}", file=sys.stderr, flush=True)
    threading.Thread(target=open, daemon=True).start()


def _wait_connected(browser, timeout: float = 10.0) -> None:
    """Стол подключился к серверу (строка статуса сменилась с «подключение…»)."""
    end = time.time() + timeout
    while time.time() < end:
        status = browser.execute("return document.getElementById('status').textContent;")
        if status and "подключение" not in status:
            return
        time.sleep(0.2)
    raise SystemExit("стол не подключился к серверу за 10 с")


def _restore_when_hidden(browser):
    """Окно свернули — развернуть: браузер не отдаёт кадры из свёрнутого окна."""
    def on_hidden() -> None:
        browser.set_window_state("normal")
        print("окно стола было свёрнуто — развернул: браузер не рисует свёрнутую страницу; "
              "чтобы окна не было вовсе, запустите с --headless", flush=True)
    return on_hidden


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:8010")
    p.add_argument("--headless", action="store_true",
                   help="без видимого окна (сервер без монитора); за героя руками не походить")
    p.add_argument("--no-view", action="store_true", help="не запускать зеркало и не публиковать")
    p.add_argument("--no-open", action="store_true", help="не открывать страницу зеркала в браузере")
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
                mirror_url = f"http://127.0.0.1:{args.view_port}/view/{session}"
                print(f"зеркало: {mirror_url}")
                if not args.no_open:
                    open_in_browser(mirror_url)
            print(f"смотрю стол {args.url} (пресет {preset.name}, калибровка {preset.calibration_id}); "
                  "Ctrl-C — остановить", flush=True)
            t0 = time.time()
            stats = watch(browser.screenshot, card_reader, number_reader, publisher, fps=args.fps,
                          on_hidden=None if args.headless else _restore_when_hidden(browser),
                          running=None if not args.seconds else
                          (lambda: time.time() - t0 < args.seconds))
            print(f"кадров обработано: {stats.frames}"
                  + (f" (пропущено, пока окно было свёрнуто: {stats.hidden})" if stats.hidden else ""))
        finally:
            browser.close()
    finally:
        if publisher is not None:
            publisher.close()


if __name__ == "__main__":
    main()
