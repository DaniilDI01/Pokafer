"""Цикл зрения: кадр страницы -> perceive -> подписчикам (пилот, зеркало).

    watch(browser.screenshot, card_reader, number_reader, publisher,
          flag_reader=…, on_perception=пилот.feed, extra=пилот.view)

Это боевой цикл глаз: кадр берётся у браузера (пиксели viewport'а), читается
целиком (pokafer/eyes/perceive.py) и уходит пилоту (on_perception — ему нужен
тот же поток Perception): трекер подтверждает состояние, а на ходу героя мозг
решает и актатор кликает (pokafer/eyes/pilot.py); пока открыта страница
отладки — ещё и в зеркало. В демо подписчик — сам трекер (герой ходит руками).

Окно браузера свободно: его можно таскать, перекрывать другими или уводить за
край экрана — браузер отдаёт кадры сам. Свернуть окно нельзя (скрытую
страницу браузер не рисует): источник кадра бросает WindowHidden, кадр
пропускается, а on_hidden решает, что делать, — обычно разворачивает окно.
Съём с монитора (capture.ScreenCapture) — запасной путь для страниц вне
нашего браузера: ему отвечает та же функция кадра grab.

Темп задаёт fps: между кадрами конвейер спит, систему не грузит.
"""
from __future__ import annotations

import io
import time
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image

from pokafer.eyes import mirror
from pokafer.eyes.cards import CardReader
from pokafer.eyes.capture import WindowHidden
from pokafer.eyes.flags import FlagReader
from pokafer.eyes.numbers import NumberReader
from pokafer.eyes.perceive import Perception, perceive

JPEG_QUALITY = 60   # кадр для страницы отладки: ~40–70 КБ при 1280x720


def jpeg_bytes(frame: Image.Image, quality: int = JPEG_QUALITY) -> bytes:
    buf = io.BytesIO()
    frame.convert("RGB").save(buf, "JPEG", quality=quality)
    return buf.getvalue()


@dataclass(frozen=True)
class WatchStats:
    frames: int      # прочитанные кадры
    hidden: int      # пропущенные: окно было скрыто (свёрнуто, вкладка не активна)


def watch(grab: Callable[[], Image.Image], card_reader: CardReader,
          number_reader: NumberReader, publisher: mirror.MirrorPublisher | None = None,
          *, fps: float = 8.0, flag_reader: FlagReader | None = None,
          on_perception: Callable[[Perception], None] | None = None,
          extra: Callable[[], dict] | None = None,
          on_hidden: Callable[[], None] | None = None,
          running: Callable[[], bool] | None = None) -> WatchStats:
    """Крутить зрение, пока running() истинно (по умолчанию — до KeyboardInterrupt).

    grab — источник кадров: как правило TableBrowser.screenshot (пиксели
    страницы из браузера). on_perception получает каждый кадр (трекер, журнал,
    тесты); extra — что добавить к состоянию зеркала после on_perception
    (например, что думает трекер). WindowHidden от источника — кадр
    пропускается и вызывается on_hidden (без него окно просто ждут, не
    спрашивая чаще периода кадра).
    """
    period = 1.0 / max(fps, 0.1)
    seq = hidden = 0
    next_at = time.perf_counter()
    try:
        while running is None or running():
            try:
                frame = grab()
            except WindowHidden:
                hidden += 1
                if on_hidden is not None:
                    on_hidden()
                time.sleep(period)
                next_at = time.perf_counter()
                continue
            seq += 1
            perception = perceive(frame, card_reader, number_reader, seq, flag_reader=flag_reader)
            if on_perception is not None:
                on_perception(perception)
            if publisher is not None and publisher.subscribed:   # без зрителя — цена ноль
                state = perception.to_state()
                if extra is not None:
                    state["extra"] = extra()          # читается после on_perception: свежее
                publisher.publish(mirror.encode(state, jpeg_bytes(perception.frame)))
            next_at += period
            pause = next_at - time.perf_counter()
            if pause > 0:
                time.sleep(pause)
            else:                      # не успели в темп — не копим отставание
                next_at = time.perf_counter()
    except KeyboardInterrupt:
        pass
    return WatchStats(seq, hidden)
