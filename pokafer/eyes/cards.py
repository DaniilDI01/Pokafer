"""Распознавание карт по шаблонам пресета — только пиксели кадра.

Карта в слоте — целиком шаблон (52 карты на каждый класс размеров): рендер
стола детерминирован, одна и та же карта даёт тот же кроп. Метрика — доля
отличий по «чернилам» (пикселям глифов), причём сравнивается только
ВНУТРЕННОСТЬ карты: кромка и скруглённые углы не считаются — там полупрозрачная
обводка мешается с сукном, а оно неоднородное (градиент) и меняется от слота
к слоту.

Состояние слота: пусто (совпал с эталоном пустого слота — сукно на его месте),
рубашка, карта (метка 'As'), иначе «неизвестно» — трекер решает, что с этим
делать. Эталоны пустого слота снимаются по месту: сукно под каждым слотом своё.

Шаблоны и зоны обязаны происходить от ОДНОЙ калибровки: набор шаблонов хранит
calibration_id, читатель сверяет его с пресетом. Сбор шаблонов нашего стола —
scripts/calibrate_pokafer.py (по месту, с проверкой детерминизма).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

from pokafer.eyes.sites import EYES_DIR, Preset, Rect

DIFF_LEVEL = 25      # пиксель «отличается», если канал разошёлся сильнее
BG_LEVEL = 215       # светлее этого — фон карты (не «чернила»)
EDGE_PX = 2          # кромка кропа не сравнивается (обводка карты + сукно)
CORNER_FRAC = 0.14   # радиус маски углов — доля ширины карты
ACCEPT_FRAC = 0.06   # порог доли отличий: не больше — та же карта / рубашка
EMPTY_FRAC = 0.02    # порог совпадения с эталоном пустого слота

_DIFF_LUT = [255 if v > DIFF_LEVEL else 0 for v in range(256)]
_INK_LUT = [255 if v < BG_LEVEL else 0 for v in range(256)]


@dataclass(frozen=True)
class Reading:
    state: str                     # empty | back | face | unknown
    label: str | None = None       # 'As' для face; иначе None
    score: float = 0.0             # доля отличий лучшего совпадения
    nearest: str | None = None     # ближайшая метка, когда state == unknown


def _compare_mask(size: tuple[int, int]) -> Image.Image:
    """'L'-маска 255 = пиксель сравнивается: внутренность карты без кромки и углов."""
    w, h = size
    r = max(round(w * CORNER_FRAC), EDGE_PX)
    px = bytearray(b"\xff") * (w * h)
    for y in range(h):
        if y < EDGE_PX or y >= h - EDGE_PX:
            px[y * w:(y + 1) * w] = b"\x00" * w
            continue
        for x in range(w):
            if x < EDGE_PX or x >= w - EDGE_PX:
                px[y * w + x] = 0
            elif ((x < r and y < r and (r - x - .5) ** 2 + (r - y - .5) ** 2 > r * r)
                  or (x >= w - r and y < r
                      and (x - w + r + .5) ** 2 + (r - y - .5) ** 2 > r * r)
                  or (x < r and y >= h - r
                      and (r - x - .5) ** 2 + (y - h + r + .5) ** 2 > r * r)
                  or (x >= w - r and y >= h - r
                      and (x - w + r + .5) ** 2 + (y - h + r + .5) ** 2 > r * r)):
                px[y * w + x] = 0
    return Image.frombytes("L", (w, h), bytes(px))


class _Ref:
    """Предобработанное изображение: каналы + маска «чернил» (для быстрого сравнения)."""

    __slots__ = ("size", "bands", "ink", "ink_n")

    def __init__(self, img: Image.Image):
        self.size = img.size
        self.bands = img.convert("RGB").split()
        self.ink = ImageChops.darker(img.convert("L").point(_INK_LUT),
                                     _compare_mask(img.size))
        self.ink_n = self.ink.histogram()[255]


def _diff(query: _Ref, ref: _Ref) -> float:
    """Доля отличий по чернилам: differs / ink-union; 1.0, если размеры не совпали."""
    if query.size != ref.size or ref.ink_n == 0:
        return 1.0
    union = ImageChops.lighter(query.ink, ref.ink)
    changed = None
    for qb, rb in zip(query.bands, ref.bands):
        d = ImageChops.difference(qb, rb).point(_DIFF_LUT)
        changed = d if changed is None else ImageChops.lighter(changed, d)
    differs = ImageChops.darker(union, changed).histogram()[255]
    return differs / union.histogram()[255]


def diff_fraction(a: Image.Image, b: Image.Image) -> float:
    """Доля отличий двух кропов по чернилам (одинаковые карты ~0, разные 0.3+)."""
    return _diff(_Ref(a), _Ref(b))


def _parse_name(stem: str) -> tuple[str, str] | None:
    """Имя файла шаблона -> (вид, ключ).

    card_As_58x82 -> ('card', 'As'); back_34x48 -> ('back', '');
    empty_board.slots_2 -> ('empty', 'board.slots[2]') — путь слота.
    """
    kind, _, rest = stem.partition("_")
    if kind == "card" and rest:
        return kind, rest.rsplit("_", 1)[0]
    if kind == "back":
        return kind, ""
    if kind == "empty" and rest:
        name, _, index = rest.rpartition("_")
        return kind, f"{name}[{index}]" if index.isdigit() else rest
    return None


class CardReader:
    """Читатель карт по шаблонам пресета: read(кадр, слот) -> Reading."""

    def __init__(self, preset: Preset, templates_dir: Path | None = None):
        base = (templates_dir or EYES_DIR / "templates") / preset.card_templates
        if not base.is_dir():
            raise FileNotFoundError(f"нет шаблонов пресета {preset.name!r}: {base} "
                                    "(соберите: scripts/calibrate_pokafer.py)")
        id_file = base / "calibration_id"
        stored = id_file.read_text(encoding="utf-8").strip() if id_file.exists() else None
        if preset.calibration_id != stored:
            raise ValueError(
                f"шаблоны пресета {preset.name!r} от другой калибровки "
                f"(пресет {preset.calibration_id!r}, шаблоны {stored!r}): "
                "зоны и шаблоны снимаются одной калибровкой — перезапустите её")
        self.calibration_id = stored
        self.preset = preset
        self._faces: dict[tuple[int, int], dict[str, _Ref]] = {}
        self._backs: dict[tuple[int, int], list[_Ref]] = {}
        self._empty: dict[str, _Ref] = {}
        for f in sorted(base.glob("*.png")):
            parsed = _parse_name(f.stem)
            if parsed is None:
                continue
            kind, key = parsed
            ref = _Ref(Image.open(f).convert("RGB"))
            if kind == "card":
                self._faces.setdefault(ref.size, {})[key] = ref
            elif kind == "back":
                self._backs.setdefault(ref.size, []).append(ref)
            else:
                self._empty[key] = ref
        self.slots_with_empty = sorted(self._empty)
        if not self._faces:
            raise ValueError(f"в {base} нет шаблонов карт (card_*.png)")

    def labels(self, size: tuple[int, int] | None = None) -> list[str]:
        """Метки шаблонов (всего или одного класса размеров)."""
        if size is not None:
            return sorted(self._faces.get(size, {}))
        return sorted({l for faces in self._faces.values() for l in faces})

    def sizes(self) -> list[tuple[int, int]]:
        """Классы размеров шаблонов карт (ширина, высота)."""
        return sorted(self._faces)

    def read(self, frame: Image.Image, rect: Rect, slot: str | None = None) -> Reading:
        """Слот кадра -> состояние и метка. slot ('board.slots[2]') подключает
        эталон пустого слота; без него пустой слот читается как unknown."""
        x, y, w, h = rect
        query = _Ref(frame.crop((x, y, x + w, y + h)))
        empty = self._empty.get(slot) if slot else None
        if empty is not None and _diff(query, empty) <= EMPTY_FRAC:
            return Reading("empty")
        for back in self._backs.get(query.size, []):
            score = _diff(query, back)
            if score <= ACCEPT_FRAC:
                return Reading("back", score=score)
        faces = self._faces.get(query.size)
        if not faces:
            return Reading("unknown", score=1.0)
        nearest, score = min(((l, _diff(query, r)) for l, r in faces.items()),
                             key=lambda t: t[1])
        if score <= ACCEPT_FRAC:
            return Reading("face", label=nearest, score=score)
        return Reading("unknown", score=score, nearest=nearest)
