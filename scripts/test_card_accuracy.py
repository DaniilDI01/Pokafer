#!/usr/bin/env python3
"""Приёмочный тест глаз: точность чтения карт на живом столе против правды.

    python scripts/test_card_accuracy.py [--seconds 60] [--bots 5]

Стол играет сам с собой (герой — чек/колл), каждый замороженный кадр читается
ОДНИМИ ПИКСЕЛЯМИ (CardReader: шаблоны пресета) и сверяется с правдой сервера.
Правда — pokafer/eyes/truth.py: мост к столу для калибровки и тестов, рантайму
глаз DOM недоступен.

Проверяются все состояния слотов: карты героя, борда и вскрытые карты, рубашки,
пустые слоты. Критерий PLAN_EYES.md: карты >= 99% (не меньше 200 проверок
карт); рубашки и пустые слоты — тоже >= 99%. Код выхода 0 — тест пройден.
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from pokafer.eyes.cards import CardReader
from pokafer.eyes.sites import preset_for_domain
from pokafer.eyes.slots import slot_rect
from pokafer.eyes.truth import TableTruthFeed, expected_cards

FACE_MIN = 200       # минимум проверенных карт за прогон
ACCURACY = 0.99


def _judge(path: str, want, got) -> tuple[str, bool]:
    """Категория проверки и верно ли прочитано."""
    if want == "back":
        return "рубашки", got.state == "back"
    if want == "covered":
        return "под рубашкой", got.state == "unknown"  # нельзя принять за пусто/карту
    if want is None:
        return "пустые", got.state == "empty"
    category = {"hero": "карты героя", "board": "карты борда"}.get(
        path.split(".")[0], "вскрытые карты")
    return category, got.state == "face" and got.label == want


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://127.0.0.1:8011")
    p.add_argument("--viewport", default="1280x720")
    p.add_argument("--seconds", type=int, default=60)
    p.add_argument("--bots", type=int, default=5)
    p.add_argument("--screen", action="store_true",
                   help="боевой путь захвата: видимое окно + пиксели с экрана (mss)")
    args = p.parse_args()
    viewport = tuple(int(x) for x in args.viewport.split("x"))
    window = (0, 0, *viewport)

    preset = preset_for_domain(args.url.split("//")[-1])
    reader = CardReader(preset)
    print(f"пресет {preset.name} (калибровка {reader.calibration_id}): "
          f"шаблонов карт {len(reader.labels())} в классах "
          f"{reader.sizes()}")

    stat: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # категория -> [ок, всего]
    failures: list[str] = []
    frames = 0
    mode = "экран видимого окна (mss)" if args.screen else "CDP-скриншот, headless"
    print(f"захват: {mode}")
    with TableTruthFeed(args.url, viewport=viewport, bots=args.bots,
                        headless=not args.screen, screen=args.screen) as feed:
        for frame in feed.frames(args.seconds):
            frames += 1
            for path, want in expected_cards(frame.truth, preset).items():
                got = reader.read(frame.image, slot_rect(preset, path, window), slot=path)
                category, ok = _judge(path, want, got)
                stat[category][1] += 1
                stat[category][0] += ok
                if not ok and len(failures) < 12:
                    failures.append(f"рука {frame.hand_no}: {path} правда={want!r} "
                                    f"прочитано={got.state}/{got.label!r} "
                                    f"(ближайшее {got.nearest!r}, отличия {got.score:.3f})")
            if frames % 25 == 0:
                done = sum(n for _, n in stat.values())
                hit = sum(k for k, _ in stat.values())
                print(f"  …кадров {frames}, слотов {done}, верно {hit / done:.1%}")
        anomalies = dict(feed.anomalies)

    faces = sum(stat[c][1] for c in ("карты героя", "карты борда", "вскрытые карты"))
    faces_ok = sum(stat[c][0] for c in ("карты героя", "карты борда", "вскрытые карты"))
    total = sum(n for _, n in stat.values())
    hit = sum(k for k, _ in stat.values())
    print(f"кадров: {frames}, проверок слотов: {total}, верно: {hit} "
          f"({hit / total:.1%})" if total else "кадров нет")
    for category, (ok, n) in sorted(stat.items()):
        print(f"  {category:16} {ok:5}/{n:<5} {ok / n:.2%}" if n else f"  {category}: нет")
    if failures:
        print("примеры ошибок:")
        for line in failures:
            print("   ", line)
    if anomalies:
        print("аномалии конвейера (кадры отброшены до проверки):", anomalies)

    rate = faces_ok / faces if faces else 0.0
    others_ok = all(ok / n >= ACCURACY for c, (ok, n) in stat.items()
                    if c not in ("карты героя", "карты борда", "вскрытые карты") and n)
    passed = rate >= ACCURACY and faces >= FACE_MIN and others_ok
    print(f"ИТОГ: карты {faces_ok}/{faces} = {rate:.2%} "
          f"(нужно >= 99% и >= {FACE_MIN} карт) — {'ПРОЙДЕН' if passed else 'ПРОВАЛЕН'}")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
