#!/usr/bin/env python3
"""Приёмочный тест боя: герой — мозг, ходы — настоящие клики актатора, судья — правда сервера.

    python scripts/test_fight_accuracy.py [--seconds 120 | --hands 100] [--bots 5]
                                          [--strategy equity] [--vs heuristic] [--headed] [--screen]

    Всё как в бою (pokafer fight): кадры читаются пикселями, трекер
    подтверждает состояние двумя одинаковыми кадрами, мозг решает, актатор
    кликает. Стенд правды (pokafer/eyes/truth.py — мост к столу для тестов,
    не рантайм) сверяет с движком:

      - каждое принятое трекером состояние — уровень и вся история действий
        (как в приёмке трекера);
      - в момент решения (стол заморожен) — срез, с которым ушёл мозг, и меню
        легальных действий: то же, что даёт движок, иначе мозг мог бы
        выбрать рейз, которого стол не примет;
      - каждый клик: трекер нашёл в истории ровно решённое.

    Критерий: уровень — ни одного расхождения; история — ни одного
    выдуманного действия; меню на решении — ни одного расхождения; кликов
    исполнено иначе — 0; бой не остановлен; недостоверных кадров не больше
    2% (иначе глаза что-то на столе не читают — например, «0» стека олл-ина).
    «Не проверено» — клик, исполнение которого прошло вместе с пропуском
    истории (быстрый стол): таких на обычном темпе нет. Код выхода 0 — пройден.

    Кадры браузер отдаёт сам. --headed — видимое окно; --screen — запасной
    путь, пиксели с монитора поверх видимого окна (mss).
"""
from __future__ import annotations

import argparse
import time
from collections import defaultdict

from pokafer.brain.api import Brain
from pokafer.brain.memory import InMemoryProfileStore
from pokafer.core.legal import legal_actions
from pokafer.core.schema import Snapshot
from pokafer.eyes.actuator import Actuator
from pokafer.eyes.cards import CardReader
from pokafer.eyes.flags import FlagReader
from pokafer.eyes.numbers import NumberReader
from pokafer.eyes.perceive import perceive
from pokafer.eyes.pilot import Pilot, Stopped
from pokafer.eyes.report import describe, describe_note, summary
from pokafer.eyes.sites import preset_for_domain
from pokafer.eyes.tracker import Tracker
from pokafer.eyes.truth import Judge, TableTruthFeed, state_key

MENU = "меню на решении"      # легальные действия в срезе трекера = легальные действия движка
DONE = "клики как решено"     # трекер нашёл в истории ровно то, что решил мозг


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:8011")
    p.add_argument("--viewport", default="1280x720")
    p.add_argument("--seconds", type=int, default=120)
    p.add_argument("--hands", type=int, default=0, help="сыграть столько рук (0 — по времени)")
    p.add_argument("--bots", type=int, default=5)
    p.add_argument("--strategy", default="equity", help="стратегия героя")
    p.add_argument("--vs", default="heuristic", help="стратегия ботов")
    p.add_argument("--seed", type=int, default=None, help="зерно мозга героя")
    p.add_argument("--headed", action="store_true", help="видимое окно браузера (не headless)")
    p.add_argument("--screen", action="store_true",
                   help="запасной путь: пиксели с экрана видимого окна (mss)")
    p.add_argument("--quiet", action="store_true", help="не печатать раздачи, только итог")
    args = p.parse_args()
    viewport = tuple(int(x) for x in args.viewport.split("x"))

    preset = preset_for_domain(args.url.split("//")[-1])
    card_reader, number_reader, flag_reader = CardReader(preset), NumberReader(preset), FlagReader(preset)
    tracker = Tracker(preset)
    brain = Brain(args.strategy, memory=InMemoryProfileStore(), seed=args.seed)
    judge = Judge()
    stat: dict = defaultdict(lambda: [0, 0])      # что -> [верно, всего]
    fails: list[str] = []
    stopped: str | None = None
    frames = 0

    def count(name: str, ok: bool, detail: str = "") -> None:
        stat[name][1] += 1
        stat[name][0] += ok
        if not ok and len(fails) < 15:
            fails.append(detail)

    def on_decision(snap: Snapshot, decision) -> None:
        """Стол заморожен до клика: срез и меню — против движка того же состояния."""
        hand = feed.hand()
        judge.judge(tracker, hand, feed.truth())
        mine = [la.key for la in legal_actions(snap)]
        want = [la.key for la in legal_actions(Snapshot.model_validate(hand))]
        count(MENU, mine == want, f"рука {hand['hand_id']}: меню трекера {mine}, движка {want}")
        count("решение легально", decision.key() in want,
              f"рука {hand['hand_id']}: мозг решил {decision.key()}, движок ждёт одно из {want}")

    mode = ("экран видимого окна (mss)" if args.screen else
            "пиксели страницы от браузера, видимое окно" if args.headed else
            "пиксели страницы от браузера, headless")
    print(f"пресет {preset.name} (калибровка {preset.calibration_id}); захват: {mode}; "
          f"герой — {args.strategy}, против {args.bots} × {args.vs}", flush=True)

    with TableTruthFeed(args.url, viewport=viewport, bots=args.bots, strategy=args.vs,
                        headless=not (args.screen or args.headed), screen=args.screen) as feed:
        pilot = Pilot(tracker, brain, Actuator(feed.browser, preset), on_decision=on_decision)
        t0 = time.time()
        end = t0 + args.seconds
        while time.time() < end and not (args.hands and pilot.hands >= args.hands):
            truth = feed.synced_truth()          # страница показывает то же, что и сервер
            if truth is None:
                continue
            perception = perceive(feed.frame(), card_reader, number_reader, flag_reader=flag_reader)
            frames += 1
            before = tracker.serial
            try:
                events = pilot.feed(perception)
            except Stopped as e:
                stopped = str(e)
                break
            if not args.quiet:
                for event in events:
                    print(describe(tracker, event), flush=True)
                level = tracker.level
                for note in pilot.take_notes():
                    print(describe_note(note, level.call if level is not None else 0.0), flush=True)
            else:
                pilot.take_notes()
            after = feed.truth()
            if tracker.serial != before and state_key(after) == state_key(truth):
                judge.judge_live(feed, tracker, truth)       # состояние принято здесь; стол не ушёл — судим
        anomalies = dict(feed.anomalies)

    decisions, done = pilot.stats["решения"], pilot.stats["исполнено как решено"]
    print(f"кадров: {frames} ({frames / max(time.time() - t0, 1e-9):.1f}/с); состояний сверено: {judge.confirmed}")
    for line in judge.report():
        print(line)
    for name in (MENU, "решение легально"):
        ok, n = stat[name]
        print(f"  {name:16} {ok:5}/{n:<5} {ok / n:.2%}" if n else f"  {name}: нет")
    print(f"  {DONE:16} {done:5}/{decisions:<5} {done / decisions:.2%}" if decisions else f"  {DONE}: нет")
    for line in summary(pilot):
        print("  " + line)
    if fails:
        print("примеры расхождений:")
        for line in fails:
            print("   ", line)
    if anomalies:
        print("аномалии конвейера (кадры отброшены до проверки):", anomalies)
    unread = sum(v for k, v in tracker.stats.items() if k.startswith("кадр недостоверный"))
    passed = (judge.passed and bool(stat[MENU][1]) and stat[MENU][0] == stat[MENU][1]
              and stat["решение легально"][0] == decisions and pilot.stats["исполнено иначе"] == 0
              and pilot.hands >= 1 and stopped is None and unread <= 0.02 * frames)
    print(f"ИТОГ: {'ПРОЙДЕН' if passed else 'ПРОВАЛЕН'} (нужно: уровень и меню — ни одного расхождения, "
          "история — ни одного выдуманного действия, все решения легальны, все клики — как решено, "
          "недостоверных кадров не больше 2%)")
    if stopped:
        print(f"СТОП: {stopped}")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
