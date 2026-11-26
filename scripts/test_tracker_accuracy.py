#!/usr/bin/env python3
"""Приёмочный тест трекера: живой стол, правда сервера против восстановленной раздачи.

    python scripts/test_tracker_accuracy.py [--seconds 90] [--bots 5] [--headed] [--screen]

    Цикл — как в бою: кадры читаются подряд, в темпе самого чтения; герой
    ходит только по подтверждённому состоянию (два одинаковых кадра) — и
    ходит не мозг, а стенд правды (чек/колл, изредка рейз или фолд): так
    проверяется именно трекер. Каждое принятое трекером состояние
    сверяется с движком сервера в тот же момент: стеки, ставки, вложения,
    банк, борд, улица, дилер, сбросившие, олл-ины, карты героя, ход героя —
    и вся история действий (GET /debug/hand, только для тестов). Правда —
    pokafer/eyes/truth.py: мост к столу для калибровки и тестов, рантайму
    глаз DOM недоступен.

    Критерий: уровень (всё, кроме истории) — расхождений 0; история — ни одного
    выдуманного действия: либо совпадает с движком действие в действие, либо
    помечена трекером пропуском. Код выхода 0 — пройден.

    Кадры браузер отдаёт сам. --headed — видимое окно; --screen — запасной
    путь, пиксели с монитора поверх видимого окна (mss).
"""
from __future__ import annotations

import argparse
import time

from pokafer.eyes.cards import CardReader
from pokafer.eyes.flags import FlagReader
from pokafer.eyes.level import key, level_of
from pokafer.eyes.numbers import NumberReader
from pokafer.eyes.perceive import perceive
from pokafer.eyes.sites import preset_for_domain
from pokafer.eyes.tracker import Tracker
from pokafer.eyes.truth import Judge, TableTruthFeed, is_frozen, state_key


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:8011")
    p.add_argument("--viewport", default="1280x720")
    p.add_argument("--seconds", type=int, default=90)
    p.add_argument("--bots", type=int, default=5)
    p.add_argument("--headed", action="store_true", help="видимое окно браузера (не headless)")
    p.add_argument("--screen", action="store_true",
                   help="запасной путь: пиксели с экрана видимого окна (mss)")
    p.add_argument("--debug", action="store_true", help="печатать контекст расхождений уровня")
    args = p.parse_args()
    viewport = tuple(int(x) for x in args.viewport.split("x"))

    preset = preset_for_domain(args.url.split("//")[-1])
    card_reader, number_reader, flag_reader = CardReader(preset), NumberReader(preset), FlagReader(preset)
    tracker = Tracker(preset)
    judge = Judge()
    mode = ("экран видимого окна (mss)" if args.screen else
            "пиксели страницы от браузера, видимое окно" if args.headed else
            "пиксели страницы от браузера, headless")
    print(f"пресет {preset.name} (калибровка {preset.calibration_id}); захват: {mode}")

    frames = 0
    with TableTruthFeed(args.url, viewport=viewport, bots=args.bots,
                        headless=not (args.screen or args.headed), screen=args.screen,
                        raise_prob=0.25, fold_prob=0.12) as feed:
        end = time.time() + args.seconds
        while time.time() < end:
            truth = feed.synced_truth()          # страница показывает то же, что и сервер
            if truth is None:
                continue
            perception = perceive(feed.frame(), card_reader, number_reader, flag_reader=flag_reader)
            before = tracker.serial
            tracker.feed(perception)
            frames += 1
            after = feed.truth()
            if state_key(after) != state_key(truth):
                continue                         # стол ушёл с состояния под съёмкой: кадр не судим
            level = tracker.level
            if tracker.serial != before:         # состояние принято ровно на этом кадре
                errors, _ = judge.judge_live(feed, tracker, truth) or ([], "")
                if errors and args.debug:
                    print("DEBUG ошибка уровня:\n  уровень трекера:", level, "\n  кадр:",
                          level_of(perception, tracker.table), "\n  правда:", after)
            if is_frozen(after) and level is not None and key(level) == key(level_of(perception, tracker.table)):
                if not feed.act_and_confirm(after):   # герой ходит только по подтверждённому состоянию
                    break
        anomalies = dict(feed.anomalies)

    print(f"кадров: {frames} ({frames / max(args.seconds, 1):.1f}/с); состояний принято: {judge.confirmed}")
    for line in judge.report():
        print(line)
    print(f"  руки: {tracker.stats['руки']}; пропуск интервалов: "
          f"{tracker.stats['интервал не восстановлен']}; недостоверных кадров: "
          f"{sum(v for k, v in tracker.stats.items() if k.startswith('кадр недостоверный'))}")
    if anomalies:
        print("аномалии конвейера (кадры отброшены до проверки):", anomalies)
    print(f"ИТОГ: {'ПРОЙДЕН' if judge.passed else 'ПРОВАЛЕН'} (нужно: уровень — ни одного расхождения, "
          "история — ни одного выдуманного действия)")
    raise SystemExit(0 if judge.passed else 1)


if __name__ == "__main__":
    main()
