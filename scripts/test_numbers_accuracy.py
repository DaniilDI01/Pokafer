#!/usr/bin/env python3
"""Приёмочный тест чисел: живой стол, правда сервера против прочитанного.

    python scripts/test_numbers_accuracy.py [--seconds 60] [--bots 5] [--screen]

Стол играет сам с собой (герой — чек/колл), каждый замороженный кадр читается
ТОЛЬКО пикселями (NumberReader: готовая модель распознавания) и сверяется с
правдой сервера. Правда — pokafer/eyes/truth.py: мост к столу для калибровки
и тестов, рантайму глаз DOM недоступен.

Проверяются стеки, банк, ставки и сумма колла, включая скрытые элементы
(фишка без ставки не должна дать числа). Критерий PLAN_EYES.md: числа >= 98%
в каждой категории. Код выхода 0 — тест пройден.

Кадры браузер отдаёт сам (по умолчанию — headless). --screen: запасной путь,
пиксели с монитора поверх видимого окна (mss).
"""
from __future__ import annotations

import argparse
import statistics
import time
from collections import defaultdict

from pokafer.eyes.numbers import NumberReader
from pokafer.eyes.sites import preset_for_domain
from pokafer.eyes.truth import NO_NUMBER, TableTruthFeed, expected_numbers, number_ok

ACCURACY = 0.98
CATEGORY = {"stack": "стеки", "bet": "ставки", "amount": "банк", "call_amount": "сумма колла"}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://127.0.0.1:8011")
    p.add_argument("--viewport", default="1280x720")
    p.add_argument("--seconds", type=int, default=60)
    p.add_argument("--bots", type=int, default=5)
    p.add_argument("--screen", action="store_true",
                   help="запасной путь: пиксели с экрана видимого окна (mss)")
    args = p.parse_args()
    viewport = tuple(int(x) for x in args.viewport.split("x"))

    preset = preset_for_domain(args.url.split("//")[-1])
    reader = NumberReader(preset)
    paths = reader.paths()
    layouts: list[list[str]] = preset.zones["meta"]["seat_layouts"]
    mode = "экран видимого окна (mss)" if args.screen else "пиксели страницы от браузера, headless"
    print(f"пресет {preset.name} (калибровка {preset.calibration_id}); зон чисел: {len(paths)}; "
          f"захват: {mode}")

    stat: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # разрез -> [верно, всего]
    fails: list[str] = []
    per_zone: list[float] = []
    per_frame: list[float] = []
    frames = 0
    with TableTruthFeed(args.url, viewport=viewport, bots=args.bots,
                        headless=not args.screen, screen=args.screen) as feed:
        for frame in feed.frames(args.seconds):
            frames += 1
            t0 = time.perf_counter()
            want = expected_numbers(frame.truth, paths, layouts)
            for path in paths:
                if path == "act.call_amount" and frame.hand_over:
                    continue          # в завершённой руке надпись кнопки — прошлая
                t1 = time.perf_counter()
                got = reader.read(frame.image, path)
                per_zone.append((time.perf_counter() - t1) * 1000)
                ok = number_ok(want[path], got)
                kind = path.rsplit(".", 1)[1]
                for key in ("ВСЁ", CATEGORY[kind],
                            f"{CATEGORY[kind]}: {'число' if want[path] not in (None, NO_NUMBER) else 'пусто' if want[path] is None else 'нет числа'}"):
                    stat[key][1] += 1
                    stat[key][0] += ok
                if not ok and len(fails) < 15:
                    fails.append(f"рука {frame.hand_no} {path}: правда={want[path]!r} "
                                 f"прочитано={got.state}/{got.text!r} (уверенность {got.score:.2f})")
            per_frame.append((time.perf_counter() - t0) * 1000)
        anomalies = dict(feed.anomalies)

    total = stat["ВСЁ"][1]
    print(f"кадров: {frames}, проверок: {total}, верно: {stat['ВСЁ'][0]} "
          f"({stat['ВСЁ'][0] / max(total, 1):.1%})")
    for key, (ok, n) in sorted((k, v) for k, v in stat.items() if k != "ВСЁ"):
        print(f"  {key:26} {ok:5}/{n:<5} {ok / n:.2%}")
    if per_zone:
        print(f"задержка: зона {statistics.median(per_zone):.1f} мс (p95 "
              f"{sorted(per_zone)[int(len(per_zone) * 0.95) - 1]:.0f}), кадр целиком "
              f"{statistics.median(per_frame):.0f} мс")
    if fails:
        print("примеры ошибок:")
        for line in fails:
            print("  ", line)
    if anomalies:
        print("аномалии конвейера (кадры отброшены до проверки):", anomalies)

    with_numbers = [k for k in stat if k.endswith(": число")]
    passed = bool(total) and all(stat[k][0] / stat[k][1] >= ACCURACY for k in
                                 ["ВСЁ", *with_numbers]) and \
        all(stat[k][0] == stat[k][1] for k in stat if not k.endswith(": число"))
    print(f"ИТОГ: {'ПРОЙДЕН' if passed else 'ПРОВАЛЕН'} (нужно >= 98% по числам и ни одного "
          "ложного числа на пустых зонах и без числа)")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
