#!/usr/bin/env python3
"""Зрение в реальном времени на нашем столе: кадр страницы -> распознавание -> зеркало.

    pokafer eyes-demo [--url http://127.0.0.1:8010] [--headless] [--no-view] [--no-open]
                      [--fps 8] [--bots 5] [--seconds 0]

    Открывает стол в управляемом браузере и в цикле читает кадры: карты, числа и
    индикаторы по пресету сайта (только пиксели — как в бою), а трекер собирает
    из кадров раздачу и пишет в терминал события: рука, действия, улицы, ход
    героя, итог. Герой не играет сам (сам он играет в бою — pokafer fight):
    ходите за него вручную и смотрите в зеркало, что видят глаза и что понял
    трекер.

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
    восприятие: в цикле глаз DOM не используется). Если наш стол не запущен,
    демо поднимает его само и останавливает в конце.
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:8010")
    p.add_argument("--headless", action="store_true",
                   help="без видимого окна (сервер без монитора); за героя руками не походить")
    p.add_argument("--no-view", action="store_true", help="не запускать зеркало и не публиковать")
    p.add_argument("--no-open", action="store_true", help="не открывать страницу зеркала в браузере")
    p.add_argument("--view-port", type=int, default=8020)
    p.add_argument("--fps", type=float, default=8.0,
                   help="темп цикла, кадров/с (чтение кадра ~4 мс, съём ~65 мс: 8–10 — предел")
    p.add_argument("--bots", type=int, default=5, help="сколько ботов посадить за стол (0 — не трогать)")
    p.add_argument("--seconds", type=float, default=0.0, help="0 — до Ctrl-C")
    args = p.parse_args()

    from pokafer.eyes import viewer
    from pokafer.eyes.browser import TableBrowser
    from pokafer.eyes.cards import CardReader
    from pokafer.eyes.flags import FlagReader
    from pokafer.eyes.loop import watch
    from pokafer.eyes.mirror import MirrorPublisher
    from pokafer.eyes.numbers import NumberReader
    from pokafer.eyes.report import describe
    from pokafer.eyes.sites import preset_for_domain
    from pokafer.eyes.table_setup import ensure_table, stop_table, wait_connected
    from pokafer.eyes.tracker import Tracker
    from selenium.common.exceptions import WebDriverException

    try:
        preset = preset_for_domain(args.url.split("//")[-1])
    except KeyError as e:
        raise SystemExit(e.args[0]) from None      # для этого домена нет пресета — глазами не прочесть
    card_reader = CardReader(preset)       # отказ на чужой калибровке/без шаблонов
    number_reader = NumberReader(preset)
    flag_reader = FlagReader(preset)       # чей ход, дилер, «рука окончена» (пусто, если их нет)
    tracker = Tracker(preset)              # кадры -> раздача (события в терминал, сводка — в зеркало)

    def on_perception(perception) -> None:
        for event in tracker.feed(perception):
            print(describe(tracker, event), flush=True)

    vw, vh = preset.viewport_ref or (1280, 720)

    session = uuid.uuid4().hex[:8]
    table = ensure_table(args.url)      # наш стол не запущен — поднимет сам; чужой рум должен отвечать
    publisher = None if args.no_view else MirrorPublisher(session, args.url)
    try:
        try:
            browser = TableBrowser(f"{args.url.rstrip('/')}/?sid=eyes-{session}", viewport=(vw, vh),
                                   headless=args.headless)
        except WebDriverException as e:
            raise SystemExit(f"стол не открылся в браузере: {args.url} — "
                             f"{(e.msg or str(e)).splitlines()[0]}") from None
        try:
            if args.bots:
                wait_connected(browser)    # клики до соединения с сервером пропадают
                browser.execute(
                    "for (let i = 0; i < arguments[0]; i++) document.getElementById('addBotBtn').click();",
                    args.bots - 1)
            if publisher is not None:
                if not viewer.is_up(args.view_port) and not viewer.start(args.view_port):
                    print("зеркало не поднялось — продолжаю без него", file=sys.stderr)
                    publisher.close()
                    publisher = None
            if publisher is not None:
                mirror_url = f"http://127.0.0.1:{args.view_port}/view/{session}"
                print(f"зеркало: {mirror_url}")
                if not args.no_open:
                    viewer.open_in_browser(mirror_url)
            print(f"смотрю стол {args.url} (пресет {preset.name}, калибровка {preset.calibration_id}); "
                  "ходите за героя руками — трекер пишет раздачу сюда и в зеркало; Ctrl-C — остановить",
                  flush=True)
            t0 = time.time()
            stats = watch(browser.screenshot, card_reader, number_reader, publisher, fps=args.fps,
                          flag_reader=flag_reader, on_perception=on_perception,
                          extra=tracker.view,
                          on_hidden=None if args.headless else
                          browser.restore_when_hidden("чтобы окна не было вовсе, запустите с --headless"),
                          running=None if not args.seconds else
                          (lambda: time.time() - t0 < args.seconds))
            print(f"кадров обработано: {stats.frames}"
                  + (f" (пропущено, пока окно было свёрнуто: {stats.hidden})" if stats.hidden else ""))
        finally:
            browser.close()
    finally:
        if publisher is not None:
            publisher.close()
        stop_table(table)


if __name__ == "__main__":
    main()
