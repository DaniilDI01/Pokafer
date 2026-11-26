#!/usr/bin/env python3
"""Приёмочный тест индикаторов: живой стол, правда сервера против прочитанного.

    python scripts/test_flags_accuracy.py [--seconds 60] [--bots 5] [--screen] [--headed]

    Индикаторы — чей ход, кнопка дилера, «рука окончена» — читаются ТОЛЬКО
    пикселями (FlagReader: по двум эталонам на зону) и сверяются с правдой
    сервера на каждом состоянии стола, ВКЛЮЧАЯ ходы ботов: замороженные кадры
    видят лишь ход героя, поэтому здесь кадры снимаются и пока стол играет
    (TableTruthFeed.samples). Правда — pokafer/eyes/truth.py: мост к столу для
    калибровки и тестов, рантайму глаз DOM недоступен.

    Критерий: ни одной ошибки — ни ложного «горит», ни пропущенного.
    Код выхода 0 — тест пройден.

    Кадры браузер отдаёт сам. --headed — в видимом окне (рендер текста и
    свечения может отличаться от headless: эталоны сняты в headless);
    --screen — запасной путь, пиксели с монитора поверх видимого окна (mss).
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from pokafer.eyes.flags import FlagReader
from pokafer.eyes.sites import preset_for_domain
from pokafer.eyes.truth import TableTruthFeed, expected_flags, flag_ok

CATEGORY = {"turn": "чей ход", "button": "кнопка дилера", "over": "рука окончена"}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:8011")
    p.add_argument("--viewport", default="1280x720")
    p.add_argument("--seconds", type=int, default=60)
    p.add_argument("--bots", type=int, default=5)
    p.add_argument("--headed", action="store_true", help="видимое окно браузера (не headless)")
    p.add_argument("--screen", action="store_true",
                   help="запасной путь: пиксели с экрана видимого окна (mss)")
    args = p.parse_args()
    viewport = tuple(int(x) for x in args.viewport.split("x"))

    preset = preset_for_domain(args.url.split("//")[-1])
    reader = FlagReader(preset)
    if not reader.paths():
        raise SystemExit("в пресете нет индикаторов ([flags]) — перекалибруйте стол")
    layouts: list[list[str]] = preset.zones["meta"]["seat_layouts"]
    mode = ("экран видимого окна (mss)" if args.screen else
            "пиксели страницы от браузера, видимое окно" if args.headed else
            "пиксели страницы от браузера, headless")
    print(f"пресет {preset.name} (калибровка {preset.calibration_id}); индикаторов: "
          f"{len(reader.paths())}; захват: {mode}")

    stat: dict = defaultdict(lambda: [0, 0])   # разрез -> [верно, всего]
    fails: list[str] = []
    frames = 0
    with TableTruthFeed(args.url, viewport=viewport, bots=args.bots,
                        headless=not (args.screen or args.headed), screen=args.screen,
                        raise_prob=0.2) as feed:
        for frame in feed.samples(args.seconds):
            frames += 1
            for path, lit in expected_flags(frame.truth, reader.paths(), layouts).items():
                got = reader.read(frame.image, path)
                ok = flag_ok(lit, got)
                kind = CATEGORY[path.rsplit(".", 1)[1]]
                for key in ("ВСЁ", kind, (kind, "горит" if lit else "погашен")):
                    stat[key][1] += 1
                    stat[key][0] += ok
                if not ok and len(fails) < 15:
                    fails.append(f"рука {frame.hand_no} {path}: правда {'горит' if lit else 'погашен'}, "
                                 f"прочитано {got.state} (близость {got.score:.2f})")
        anomalies = dict(feed.anomalies)

    total = stat["ВСЁ"][1]
    print(f"кадров: {frames}, проверок: {total}, верно: {stat['ВСЁ'][0]} "
          f"({stat['ВСЁ'][0] / max(total, 1):.2%})")
    for kind in CATEGORY.values():
        for state in ("горит", "погашен"):
            ok, n = stat[(kind, state)]
            print(f"  {kind:16} {state:8} {ok:5}/{n:<5}")
    if fails:
        print("примеры ошибок:")
        for line in fails:
            print("  ", line)
    if anomalies:
        print("аномалии конвейера (кадры отброшены до проверки):", anomalies)
    lit = {key: stat[key][1] for key in stat if isinstance(key, tuple) and key[1] == "горит"}
    passed = bool(total) and all(ok == n for ok, n in stat.values()) and all(lit.values())
    print(f"ИТОГ: {'ПРОЙДЕН' if passed else 'ПРОВАЛЕН'} (нужно: ни одной ошибки, и каждый вид "
          "индикатора горел хотя бы раз)")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
