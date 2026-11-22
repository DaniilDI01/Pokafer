"""Захват кадров экрана: mss (боевой режим) или файл (тесты).

Распознаватели (cards/numbers/tracker) работают с PIL.Image — источник
кадров им безразличен. Продакшн-захват берёт прямоугольник окна/области
в координатах монитора.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image


def capture_region(x: int, y: int, w: int, h: int) -> Image.Image:
    """Кадр области экрана (x, y, w, h) в координатах монитора -> PIL.Image."""
    import mss

    with mss.mss() as sct:
        shot = sct.grab({"left": x, "top": y, "width": w, "height": h})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def capture_full() -> tuple[Image.Image, dict]:
    """Кадр всего монитора + его геометрия (для интерактивной калибровки)."""
    import mss

    with mss.mss() as sct:
        mon = sct.monitors[0]  # весь виртуальный экран
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return img, {"left": mon["left"], "top": mon["top"],
                     "width": mon["width"], "height": mon["height"]}


def load_frame(path: str | Path) -> Image.Image:
    """Кадр из файла (тесты/калибровка без экрана)."""
    return Image.open(path).convert("RGB")
