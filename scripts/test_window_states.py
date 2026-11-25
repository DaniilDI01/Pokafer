#!/usr/bin/env python3
"""Приёмка свободного окна: глаза читают и актатор кликают при любом окне браузера.

    python scripts/test_window_states.py [--url http://127.0.0.1:8011] [--actions 12]

Требование исследователя: глаза не привязаны к месту на экране — окно стола
можно таскать и перекрывать другими окнами. Кадры глаза берут у самого
браузера, и здесь это проверяется тремя состояниями окна (нужен рабочий стол):

  видимое     — норма;
  перекрытое  — поверх стола развёрнуто чужое окно: кадры продолжают
                приходить, а окно стола НЕ поднимается над чужим;
  свёрнутое   — свёрнутую страницу браузер не рисует: кадр не запрашивается
                (иначе запрос виснет навсегда), окно разворачивается, чтение
                продолжается.

В каждом состоянии герой ходит только РЕАЛЬНЫМИ кликами актатора (курсор
после клика уходит с кнопки), а перед каждым ходом замороженный кадр читается
пикселями целиком (perceive) и сверяется с правдой сервера (pokafer/eyes/
truth.py — мост для тестов, не рантайм). Зачёт: ни одной ошибки чтения, все
клики приняты, свёрнутое окно не повисло; на X11 ещё и то, что чужое окно
осталось над столом.

«Стол не поднялся» проверяется по порядку окон у оконного менеджера
(xprop, X11), а не по пикселям монитора: поверх обоих окон может работать
человек, и картина на экране ему принадлежит. Окна друг от друга отличаются
профилем Chrome, а не заголовком страницы.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
import time
from collections import defaultdict, namedtuple
from contextlib import contextmanager
from urllib.parse import quote

from pokafer.eyes.actuator import Actuator
from pokafer.eyes.capture import WindowHidden
from pokafer.eyes.cards import CardReader
from pokafer.eyes.numbers import NumberReader
from pokafer.eyes.perceive import perceive
from pokafer.eyes.sites import preset_for_domain
from pokafer.eyes.truth import TableTruthFeed, card_ok, expected_cards, expected_numbers, number_ok

COVER_TITLE = "Pokafer — тест глаз: окно-перекрытие"
COVER_PAGE = (
    f"<title>{COVER_TITLE}</title>"
    "<body style='margin:0;height:100vh;display:grid;place-items:center;"
    "background:#1c222d;color:#e8edf4;font:20px/1.6 system-ui,sans-serif;text-align:center'>"
    "<div>Это окно <b>намеренно</b> перекрывает покерный стол: тест проверяет,<br>"
    "что глаза читают стол и под другим окном.<br>"
    "<span style='color:#8a93a6'>Оно закроется само — ничего нажимать не нужно.</span></div>"
)

Eyes = namedtuple("Eyes", "preset cards numbers paths layouts")


@contextmanager
def cover_window():
    """Чужое развёрнутое окно поверх всего экрана (отдельный браузер); отдаёт путь его профиля."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions

    profile = tempfile.mkdtemp(prefix="pokafer-cover-")
    opts = ChromeOptions()
    opts.add_argument("--start-maximized")
    opts.add_argument(f"--user-data-dir={profile}")
    cover = webdriver.Chrome(options=opts)
    try:
        # страница с подписью: полноэкранное окно без объяснения выглядит как авария
        cover.get("data:text/html;charset=utf-8," + quote(COVER_PAGE))
        cover.execute_cdp_cmd("Page.bringToFront", {})      # на занятом столе новое окно может открыться позади
        yield profile
    finally:
        cover.quit()
        shutil.rmtree(profile, ignore_errors=True)


def cover_is_above(table_profile: str | None, cover_profile: str) -> bool | None:
    """Выше ли перекрытие, чем окно стола, в стопке окон; None — порядка не узнать (не X11).

    Окна различаются по профилю Chrome: его путь вписан в класс окна (WM_CLASS).
    Заголовок для этого не годится — у стола в другом демо он тот же самый.
    """
    if table_profile is None:
        return None
    try:
        tree = subprocess.run(["xwininfo", "-root", "-tree"], capture_output=True, text=True,
                              timeout=10).stdout
        out = subprocess.run(["xprop", "-root", "_NET_CLIENT_LIST_STACKING"], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if "#" not in out:
        return None
    order = [int(w, 16) for w in re.findall(r"0x[0-9a-f]+", out.split("#", 1)[1])]
    place = {}
    for wid, wm_class in re.findall(r'\s*(0x[0-9a-f]+) "[^"]*": \("([^"]*)"', tree):
        for name, profile in (("table", table_profile), ("cover", cover_profile)):
            if profile in wm_class and int(wid, 16) in order:
                place[name] = order.index(int(wid, 16))
    if len(place) < 2:
        return None
    return place["cover"] > place["table"]


def judge(feed: TableTruthFeed, eyes: Eyes, stat: dict, failures: list[str], tag: str) -> None:
    """Замороженный кадр -> чтение пикселями -> сверка с правдой."""
    truth = feed.wait_frozen()
    if truth is None:
        raise SystemExit("стол не замораживается: ход не у героя")
    feed.wait_dom_sync(truth)
    image = feed.settled()
    if image is None:
        raise SystemExit("краска не устоялась: кадр снять не удалось")
    seen = perceive(image, eyes.cards, eyes.numbers)
    readings = {c.slot.path: c.reading for c in seen.cards}
    for path, want in expected_cards(truth, eyes.preset).items():
        got = readings[path]
        ok = card_ok(want, got)
        stat[(tag, "карты")][1] += 1
        stat[(tag, "карты")][0] += ok
        if not ok:
            failures.append(f"{tag} {path}: правда {want!r}, прочитано {got.state}/{got.label!r}")
    for path, want in expected_numbers(truth, eyes.paths, eyes.layouts).items():
        if path == "act.call_amount" and truth.get("hand_over"):
            continue                  # в завершённой руке надпись кнопки — прошлая
        got = seen.numbers[path]
        ok = number_ok(want, got)
        stat[(tag, "числа")][1] += 1
        stat[(tag, "числа")][0] += ok
        if not ok:
            failures.append(f"{tag} {path}: правда {want!r}, прочитано {got.state}/{got.text!r}")


def step(feed: TableTruthFeed, actuator: Actuator) -> bool:
    """Один ход героя РЕАЛЬНЫМ кликом актатора: принят ли сервером."""
    truth = feed.truth()
    (actuator.next_hand if truth.get("hand_over") else actuator.check_or_call)()
    return feed.accepted(truth)


def run(feed: TableTruthFeed, actuator: Actuator, eyes: Eyes, name: str, actions: int,
        stat: dict, failures: list[str]) -> None:
    for _ in range(actions):
        judge(feed, eyes, stat, failures, name)
        accepted = step(feed, actuator)
        stat[(name, "клики")][1] += 1
        stat[(name, "клики")][0] += accepted
        if not accepted:
            failures.append(f"{name}: реальный клик не принят сервером")
            return


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:8011")
    p.add_argument("--actions", type=int, default=12, help="ходов героя в каждом состоянии окна")
    p.add_argument("--bots", type=int, default=5)
    args = p.parse_args()

    preset = preset_for_domain(args.url.split("//")[-1])
    numbers = NumberReader(preset)
    eyes = Eyes(preset, CardReader(preset), numbers, numbers.paths(),
                preset.zones["meta"]["seat_layouts"])
    stat: dict = defaultdict(lambda: [0, 0])      # (состояние, что) -> [верно, всего]
    failures: list[str] = []
    kept_hidden = False
    above: bool | None = None

    with TableTruthFeed(args.url, bots=args.bots, headless=False) as feed:
        actuator = Actuator(feed.browser, preset)
        run(feed, actuator, eyes, "видимое", args.actions, stat, failures)

        # перекрытое: чужое развёрнутое окно поверх стола
        with cover_window() as cover_profile:
            time.sleep(1.0)
            above = cover_is_above(feed.browser.profile_dir, cover_profile)
            if above is False:
                raise SystemExit("перекрывающее окно не легло поверх стола: "
                                 "проверка перекрытого состояния невозможна")
            run(feed, actuator, eyes, "перекрытое", args.actions, stat, failures)
            above = cover_is_above(feed.browser.profile_dir, cover_profile)
        if above is False:
            failures.append("окно стола поднялось над перекрывающим окном")

        # свёрнутое: страница скрыта — кадр не берётся (иначе виснет), окно разворачивается
        feed.browser.set_window_state("minimized")
        time.sleep(0.8)
        if feed.browser.execute("return document.visibilityState;") != "hidden":
            raise SystemExit("окно не свернулось: проверка свёрнутого состояния невозможна")
        try:
            feed.browser.screenshot()
            failures.append("со свёрнутым окном запрос кадра вернулся, а не бросил WindowHidden")
        except WindowHidden:
            kept_hidden = True
        feed.browser.set_window_state("normal")
        time.sleep(0.6)
        run(feed, actuator, eyes, "после разворота", args.actions, stat, failures)

    print(f"пресет {preset.name} (калибровка {preset.calibration_id}); ходов героя "
          f"в каждом состоянии: {args.actions}")
    for (tag, kind), (ok, total) in sorted(stat.items()):
        print(f"  {tag:16} {kind:6} {ok:4}/{total:<4} {ok / total:.0%}")
    print(f"  свёрнутое окно: {'кадр не запрошен, окно развёрнуто' if kept_hidden else 'НЕ обработано'}")
    print("  чужое окно над столом после чтения: "
          + ("да" if above else ("НЕТ — окно стола поднялось" if above is False else "не проверено: нет xprop (не X11)")))
    for line in failures[:20]:
        print("   ОШИБКА:", line)
    passed = kept_hidden and not failures
    print(f"ИТОГ: {'ПРОЙДЕН' if passed else 'ПРОВАЛЕН'} (нужно: ни одной ошибки чтения, "
          "все клики приняты, свёрнутое окно не повисло; на X11 — чужое окно осталось над столом)")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
