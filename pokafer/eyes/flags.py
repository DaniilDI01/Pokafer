"""Чтения-индикаторы: зона кадра -> «вкл» / «выкл» / «неизвестно» — только пиксели.

Чей сейчас ход, где кнопка дилера, окончена ли рука — на столе это не числа и
не карты, а ВИД небольшого участка: у ходящего места зажглась рамка аватара,
у имени появился значок «D», кнопка следующей руки стала яркой. Такой участок
читается по двум эталонам, снятым калибровкой по месту (как эталоны пустых
слотов у карт): «вкл» и «выкл».

Метрика — среднее отличие кропов по каналам, но в долях расстояния МЕЖДУ
эталонами: порог не зависит от того, насколько контрастен индикатор. Кадр
близко к «вкл» — on, к «выкл» — off; далеко от обоих (курсор лёг на кнопку,
всплыла подсказка, анимация) — unknown, и трекер не считает это информацией.

Индикаторы пресета — секция [flags]: «<владелец>.<вид>» -> зона. Виды:
turn (чей ход), button (кнопка дилера), over (hand.over — рука окончена).
Эталоны — в папке шаблонов: flag_<владелец>.<вид>_on.png и _off.png.

Зависимости — только Pillow.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from pokafer.eyes.sites import Preset, Rect, template_dir

ACCEPT_FRAC = 0.3    # до ближайшего эталона — не более этой доли расстояния между эталонами
MARGIN = 2.5         # и во столько раз ближе, чем до другого


@dataclass(frozen=True)
class FlagReading:
    state: str       # on | off | unknown
    score: float = 0.0   # расстояние до ближайшего эталона в долях расстояния между ними


def distance(a: Image.Image, b: Image.Image) -> float:
    """Среднее отличие двух кропов по каналам: 0 — совпали пиксель в пиксель."""
    if a.size != b.size:
        return float("inf")
    means = ImageStat.Stat(ImageChops.difference(a, b)).mean
    return sum(means) / len(means)


class FlagReader:
    """Читатель индикаторов пресета: read(кадр, зона) -> FlagReading.

    Пресет без секции [flags] — читатель без зон (.answers пусты, paths() = []).
    """

    def __init__(self, preset: Preset, templates_dir: Path | None = None):
        self.preset = preset
        self.zones: dict[str, Rect] = dict(preset.zones.get("flags", {}))
        self._on: dict[str, Image.Image] = {}
        self._off: dict[str, Image.Image] = {}
        self._sep: dict[str, float] = {}
        if not self.zones:
            return
        base = template_dir(preset, templates_dir)
        for path in self.zones:
            on = Image.open(base / f"flag_{path}_on.png").convert("RGB")
            off = Image.open(base / f"flag_{path}_off.png").convert("RGB")
            self._on[path], self._off[path] = on, off
            self._sep[path] = distance(on, off)

    def paths(self) -> list[str]:
        """Индикаторы пресета (по алфавиту)."""
        return sorted(self.zones)

    def zone(self, path: str, window: tuple[int, int, int, int] = (0, 0, 0, 0)) -> Rect:
        """Зона индикатора в пикселях окна (имена с точками — потому не через Preset.zone)."""
        return self.preset.to_px(self.zones[path], window)

    def read(self, frame: Image.Image, path: str,
             window: tuple[int, int, int, int] | None = None) -> FlagReading:
        """Индикатор зоны path ('hero.turn', 'hand.over')."""
        if path not in self.zones:
            raise KeyError(f"индикатор {path!r} не описан в пресете ([flags])")
        x, y, w, h = self.zone(path, window or (0, 0, 0, 0))
        crop = frame.crop((x, y, x + w, y + h)).convert("RGB")
        d_on = distance(crop, self._on[path])
        d_off = distance(crop, self._off[path])
        sep = self._sep[path]
        if sep <= 0:                      # эталоны неразличимы: калибровка ни на что не годится
            return FlagReading("unknown", 1.0)
        if d_on <= ACCEPT_FRAC * sep and d_on <= d_off / MARGIN:
            return FlagReading("on", d_on / sep)
        if d_off <= ACCEPT_FRAC * sep and d_off <= d_on / MARGIN:
            return FlagReading("off", d_off / sep)
        return FlagReading("unknown", min(d_on, d_off) / sep)


__all__ = ["ACCEPT_FRAC", "MARGIN", "FlagReading", "FlagReader", "distance"]
