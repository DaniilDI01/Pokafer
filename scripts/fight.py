#!/usr/bin/env python3
"""Бой: герой играет за столом только глазами — кадры → трекер → мозг → клик.

    pokafer fight [--url http://127.0.0.1:8010] [--strategy equity] [--vs heuristic]
                  [--bots 5] [--hands 100] [--seed 0] [--record data/snapshots/fight.jsonl]
                  [--headless] [--no-view] [--no-open]

    Глаза читают кадр страницы (только пиксели), трекер собирает из кадров
    раздачу и подтверждает состояние двумя одинаковыми кадрами, мозг решает
    по его Snapshot (та же стратегия, что у ботов и в HTTP-API мозга), актатор
    кликает по кнопкам пресета настоящими событиями мыши. Ведёт всё это пилот
    (pokafer/eyes/pilot.py): одно решение на подтверждённое состояние; клик
    обязан сработать (не сработал — повтор тем же решением, не более трёх
    раз, затем стоп); по неполной истории — только фолд/чек/колл.

    Терминал пишет раздачу и решения героя, в конце — итог (руки, исход по
    стекам, решения, повторы). --record — снапшот и решение героя на каждом
    ходу в JSONL (формат replay: читается replay_offline и статистикой
    оппонентов) — живые раздачи становятся материалом для анализа. Зеркало — страница «что видят глаза»:
    http://127.0.0.1:8020/view/<сессия>, открывается сама и показывает то же
    живьём (как у eyes-demo).

    --hands 0 — до Ctrl-C. --bots / --vs — сколько ботов посадить и с какой
    стратегией (настраивается DOM страницы до цикла — это подготовка, не
    восприятие). Если наш стол не запущен, бой поднимает его сам и
    останавливает в конце; стол, запущенный отдельно, не трогает. Окно браузера можно таскать и перекрывать (кадры отдаёт сам
    браузер); свёрнутое окно разворачивается само; чтобы окна не было вовсе —
    --headless.
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
    p.add_argument("--strategy", default="equity", help="стратегия героя (pokafer status — список)")
    p.add_argument("--vs", default="heuristic", help="стратегия ботов за столом")
    p.add_argument("--bots", type=int, default=5, help="сколько ботов посадить за стол")
    p.add_argument("--hands", type=int, default=100, help="сколько рук сыграть (0 — до Ctrl-C)")
    p.add_argument("--seed", type=int, default=None, help="зерно мозга героя: бой воспроизводим")
    p.add_argument("--record", default=None,
                   help="JSONL: снапшот и решение героя на каждом ходу (как demo --record)")
    p.add_argument("--headless", action="store_true", help="без видимого окна (сервер без монитора)")
    p.add_argument("--no-view", action="store_true", help="не запускать зеркало и не публиковать")
    p.add_argument("--no-open", action="store_true", help="не открывать страницу зеркала в браузере")
    p.add_argument("--view-port", type=int, default=8020)
    p.add_argument("--fps", type=float, default=8.0, help="темп цикла, кадров/с")
    args = p.parse_args()

    from pokafer.adapters.replay_file import ReplayFile
    from pokafer.brain.api import Brain
    from pokafer.brain.memory import InMemoryProfileStore
    from pokafer.eyes import viewer
    from pokafer.eyes.actuator import Actuator
    from pokafer.eyes.browser import TableBrowser
    from pokafer.eyes.cards import CardReader
    from pokafer.eyes.flags import FlagReader
    from pokafer.eyes.loop import watch
    from pokafer.eyes.mirror import MirrorPublisher
    from pokafer.eyes.numbers import NumberReader
    from pokafer.eyes.pilot import Pilot, Stopped
    from pokafer.eyes.report import describe, describe_note, summary
    from pokafer.eyes.sites import preset_for_domain
    from pokafer.eyes.table_setup import ensure_table, seat_bots, stop_table, wait_connected
    from pokafer.eyes.tracker import Tracker
    from selenium.common.exceptions import WebDriverException

    try:
        preset = preset_for_domain(args.url.split("//")[-1])
    except KeyError as e:
        raise SystemExit(e.args[0]) from None      # для этого домена нет пресета — глазами не прочесть
    card_reader, number_reader, flag_reader = CardReader(preset), NumberReader(preset), FlagReader(preset)
    tracker = Tracker(preset)
    brain = Brain(args.strategy, memory=InMemoryProfileStore(), seed=args.seed)

    def on_decision(snap, decision) -> None:           # стол заморожен: снапшот — то, по чему решал мозг
        if args.record:
            ReplayFile.append(args.record, snap, decision.model_dump())

    def on_perception(perception) -> None:
        for event in pilot.feed(perception):
            print(describe(tracker, event), flush=True)
        level = tracker.level
        for note in pilot.take_notes():
            print(describe_note(note, level.call if level is not None else 0.0), flush=True)

    vw, vh = preset.viewport_ref or (1280, 720)
    session = uuid.uuid4().hex[:8]
    table = ensure_table(args.url)      # наш стол не запущен — поднимет сам; чужой рум должен отвечать
    publisher = None if args.no_view else MirrorPublisher(session, args.url)
    stopped = None
    try:
        try:
            browser = TableBrowser(f"{args.url.rstrip('/')}/?sid=fight-{session}", viewport=(vw, vh),
                                   headless=args.headless)
        except WebDriverException as e:
            raise SystemExit(f"стол не открылся в браузере: {args.url} — "
                             f"{(e.msg or str(e)).splitlines()[0]}") from None
        try:
            pilot = Pilot(tracker, brain, Actuator(browser, preset), on_decision=on_decision)
            wait_connected(browser)         # команды до соединения с сервером пропадают
            if args.bots:
                seat_bots(browser, args.bots, args.vs)
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
            print(f"бой: герой — {args.strategy}, против {args.bots} × {args.vs}; стол {args.url} "
                  f"(пресет {preset.name}, калибровка {preset.calibration_id}); "
                  + (f"{args.hands} рук" if args.hands else "до Ctrl-C"), flush=True)
            t0 = time.time()
            try:
                watch(browser.screenshot, card_reader, number_reader, publisher, fps=args.fps,
                      flag_reader=flag_reader, on_perception=on_perception, extra=pilot.view,
                      on_hidden=None if args.headless else
                      browser.restore_when_hidden("чтобы окна не было вовсе, запустите с --headless"),
                      running=None if not args.hands else (lambda: pilot.hands < args.hands))
            except Stopped as e:
                stopped = str(e)
            print(f"— итог боя ({time.time() - t0:.0f} с) —")
            for line in summary(pilot):
                print(line)
            if args.record and pilot.stats["решения"]:
                print(f"записано решений героя: {pilot.stats['решения']} -> {args.record}")
            if stopped:
                print(f"СТОП: {stopped}", file=sys.stderr)
        finally:
            browser.close()
    finally:
        if publisher is not None:
            publisher.close()
        stop_table(table)
    raise SystemExit(2 if stopped else 0)


if __name__ == "__main__":
    main()
