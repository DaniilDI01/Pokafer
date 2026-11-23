"""Актатор глаз: действия по зонам пресета (клики, клавиатура, рейз).

Работает ТОЛЬКО по координатам из калибровки — реальными вводными
событиями мыши/клавиатуры, без чтения DOM (DOM разрешён калибровке,
не рантайму). Рейз: пресет-кнопки или точная сумма через поле ввода
и клавиатуру.
"""
from __future__ import annotations

from pokafer.eyes.browser import TableBrowser
from pokafer.eyes.sites import Preset


class Actuator:
    def __init__(self, browser: TableBrowser, preset: Preset):
        self.browser = browser
        self.preset = preset

    def _center(self, path: str) -> tuple[int, int]:
        vw, vh = self.browser.viewport
        x, y, w, h = self.preset.zone(path, (0, 0, vw, vh))
        return x + w // 2, y + h // 2

    def click(self, path: str) -> None:
        x, y = self._center(path)
        self.browser.click_at(x, y)

    # ---------- готовые действия ----------

    def fold(self) -> None:
        self.click("act.fold")

    def check_or_call(self) -> None:
        self.click("act.check")

    def next_hand(self) -> None:
        self.click("act.next")

    def raise_preset(self, which: str = "min") -> None:
        """which: min | half | pot | max."""
        self.click(f"act.preset_{which}")
        self.click("act.raise")

    def raise_to(self, amount: float, legal_min: float, legal_max: float) -> None:
        """Точная сумма: клик в поле ввода, выделение, ввод с клавиатуры, рейз.

        Сумма задаётся трекером (он читает min/max рейза из зоны поля/слайдера).
        """
        if amount >= legal_max - 1e-9:
            self.raise_preset("max")
            return
        vw, vh = self.browser.viewport
        x, y, w, h = self.preset.zone("act.input", (0, 0, vw, vh))
        cx, cy = x + w // 2, y + h // 2
        # тройной клик = выделить текущее значение в поле
        self.browser.click_at(cx, cy)
        self.browser.click_at(cx, cy)
        self.browser.click_at(cx, cy)
        self.browser.type_text(str(round(amount, 2)))
        self.click("act.raise")
