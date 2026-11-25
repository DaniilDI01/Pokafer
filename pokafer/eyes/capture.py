"""Захват кадров с монитора: mss (запасной путь) или файл (тесты).

Распознаватели (cards/numbers/tracker) работают с PIL.Image — источник
кадров им безразличен. Основной цикл глаз берёт кадры у браузера
(TableBrowser.screenshot): окно можно таскать и сворачивать. Съём с монитора
остался для страниц вне нашего браузера и для проверки «как на экране» в
тестах (--screen): прямоугольник viewport'а на экране находит
TableBrowser.locate_on_screen(); окно должно быть видно целиком и ничем не
перекрыто — иначе в кадре чужие пиксели.
"""
from __future__ import annotations

import warnings
from pathlib import Path

from PIL import Image

Rect = tuple[int, int, int, int]  # x, y, w, h в координатах ЭКРАНА


class WindowHidden(RuntimeError):
    """Страница скрыта: окно браузера свёрнуто или вкладка стола не активна.

    Скрытую страницу браузер не рисует — запрос кадра повис бы навсегда
    (проверено на Chrome 139), поэтому источник кадра проверяет это заранее и
    бросает WindowHidden. Свернуть окно без разворачивания нельзя; убрать его
    с глаз можно, перекрыв другим окном, уведя за край экрана или запустив
    браузер без окна (--headless).
    """


def _open_screen():
    import mss

    with warnings.catch_warnings():  # mss 10: класс MSS, фабрика mss() устарела
        warnings.simplefilter("ignore", DeprecationWarning)
        return (getattr(mss, "MSS", None) or mss.mss)()


def _to_image(shot) -> Image.Image:
    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


class ScreenCapture:
    """Постоянный захват области экрана: один контекст mss на цикл кадров.

    grab() возвращает кадр ровно (w, h) — «кадр окна» для распознавателей.
    Экран может отставать от страницы на кадр-другой: устойчивость кадра
    (два подряд одинаковых снимка) — забота трекера, не захвата.
    """

    def __init__(self, region: Rect):
        self.region = region
        self._sct = None

    def __enter__(self) -> "ScreenCapture":
        self._sct = _open_screen()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._sct is not None:
            self._sct.close()
            self._sct = None

    def grab(self) -> Image.Image:
        if self._sct is None:  # разовый снимок без `with`
            with ScreenCapture(self.region) as cap:
                return cap.grab()
        x, y, w, h = self.region
        return _to_image(self._sct.grab({"left": x, "top": y, "width": w, "height": h}))


def capture_region(x: int, y: int, w: int, h: int) -> Image.Image:
    """Разовый кадр области экрана (x, y, w, h) в координатах монитора."""
    return ScreenCapture((x, y, w, h)).grab()


def capture_full() -> tuple[Image.Image, dict]:
    """Кадр всего виртуального экрана (все мониторы) + его геометрия."""
    sct = _open_screen()
    try:
        mon = sct.monitors[0]
        return _to_image(sct.grab(mon)), {"left": mon["left"], "top": mon["top"],
                                          "width": mon["width"], "height": mon["height"]}
    finally:
        sct.close()


def load_frame(path: str | Path) -> Image.Image:
    """Кадр из файла (тесты/калибровка без экрана)."""
    return Image.open(path).convert("RGB")
