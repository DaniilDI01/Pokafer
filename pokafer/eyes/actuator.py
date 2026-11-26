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
        self.park = self._center("act.park") if "park" in self.preset.node("act") else None

    def _center(self, path: str) -> tuple[int, int]:
        vw, vh = self.browser.viewport
        x, y, w, h = self.preset.zone(path, (0, 0, vw, vh))
        return x + w // 2, y + h // 2

    def click(self, path: str) -> None:
        x, y = self._center(path)
        # курсор уходит с кнопки (act.park пресета — пустая точка стола):
        # под оставленным курсором кнопка подсвечена, и глаза не узнают её число
        self.browser.click_at(x, y, self.park)

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

    def raise_to(self, amount: float, all_in_to: float) -> None:
        """Поднять «до» amount: олл-ин — кнопкой пресета, иную сумму — вводом в поле.

        Клик ставит курсор в поле, Ctrl+A выделяет его текущее значение
        (минимальный рейз), ввод с клавиатуры заменяет. Олл-ин — сумма
        «до» из легальных действий трекера (их знает пилот); минимальный
        рейз с экрана не читается.
        """
        if amount >= all_in_to - 1e-9:
            self.raise_preset("max")
            return
        vw, vh = self.browser.viewport
        x, y, w, h = self.preset.zone("act.input", (0, 0, vw, vh))
        self.browser.click_at(x + w // 2, y + h // 2, self.park)
        self.browser.select_all()
        self.browser.type_text(str(round(amount, 2)))
        self.click("act.raise")
