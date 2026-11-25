"""Цикл зрения: захват кадра окна -> perceive -> подписчикам зеркала.

    watch(browser, card_reader, number_reader, publisher)

Это первый настоящий боевой цикл глаз: кадр берётся с экрана (видимое окно
управляемого браузера) или CDP-скриншотом (headless, для тестов), читается
целиком (pokafer/eyes/perceive.py) и, пока открыта страница отладки,
публикуется в зеркало. Трекер, мозг и актуатор надстраиваются сверху: им
нужен тот же поток Perception.

Темп задаёт fps: между кадрами конвейер спит, систему не грузит.
"""
from __future__ import annotations

import io
import time
from collections.abc import Callable

from PIL import Image

from pokafer.eyes import mirror
from pokafer.eyes.browser import TableBrowser
from pokafer.eyes.capture import ScreenCapture
from pokafer.eyes.cards import CardReader
from pokafer.eyes.numbers import NumberReader
from pokafer.eyes.perceive import Perception, perceive

JPEG_QUALITY = 60   # кадр для страницы отладки: ~40–70 КБ при 1280x720


def jpeg_bytes(frame: Image.Image, quality: int = JPEG_QUALITY) -> bytes:
    buf = io.BytesIO()
    frame.convert("RGB").save(buf, "JPEG", quality=quality)
    return buf.getvalue()


class FrameSource:
    """Откуда берутся кадры: экран (видимое окно) или CDP-скриншот (headless)."""

    def __init__(self, browser: TableBrowser, screen: bool):
        self.browser = browser
        self._capture: ScreenCapture | None = None
        if screen:
            x, y = browser.locate_on_screen()      # где окно лежит на экране
            vw, vh = browser.viewport
            self._capture = ScreenCapture((x, y, vw, vh))

    def grab(self) -> Image.Image:
        if self._capture is not None:
            return self._capture.grab()
        return self.browser.screenshot()

    def close(self) -> None:
        if self._capture is not None:
            self._capture.close()


def watch(browser: TableBrowser, card_reader: CardReader, number_reader: NumberReader,
          publisher: mirror.MirrorPublisher | None = None, *, fps: float = 4.0,
          screen: bool = True, on_perception: Callable[[Perception], None] | None = None,
          running: Callable[[], bool] | None = None) -> int:
    """Крутить зрение, пока running() истинно (по умолчанию — до KeyboardInterrupt).

    on_perception получает каждый кадр (трекер, журнал, тесты). Возвращает
    число обработанных кадров.
    """
    source = FrameSource(browser, screen)
    period = 1.0 / max(fps, 0.1)
    seq = 0
    next_at = time.perf_counter()
    try:
        while running is None or running():
            frame = source.grab()
            seq += 1
            perception = perceive(frame, card_reader, number_reader, seq)
            if on_perception is not None:
                on_perception(perception)
            if publisher is not None and publisher.subscribed:   # без зрителя — цена ноль
                publisher.publish(mirror.encode(perception.to_state(), jpeg_bytes(perception.frame)))
            next_at += period
            pause = next_at - time.perf_counter()
            if pause > 0:
                time.sleep(pause)
            else:                      # не успели в темп — не копим отставание
                next_at = time.perf_counter()
    except KeyboardInterrupt:
        pass
    finally:
        source.close()
    return seq
