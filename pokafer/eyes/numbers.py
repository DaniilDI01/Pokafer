"""Распознавание чисел по зонам пресета — только пиксели кадра.

Шрифт, кегль и сглаживание текста на разных сайтах разные, и рендер зависит
от среды (headless/видимое окно, видеокарта, шрифты машины). Пиксельные
шаблоны под конкретный шрифт пришлось бы снимать и отлаживать на каждом сайте
и в каждой среде. Поэтому числа читает готовая модель распознавания строк
(PP-OCRv5, мобильная; работает локально через ONNX Runtime): она обучена на
огромном множестве шрифтов и в калибровке под сайт не нуждается. Замеры на
нашем столе и на чужих шрифтах — docs/PLAN_EYES.md.

Зона числа охватывает само число (и знак валюты, если он вплотную): модель
читает всю поданную картинку как одну строку, поэтому подписи вроде «Колл »
или «Банк: » в зону не входят. Что есть элемент на месте, а что скрыт (фишка
без ставки), решается по фону зоны: его яркость знает пресет. Пустые поля
зоны перед распознаванием срезаются (tighten): на плотном кропе модель
заметно точнее — одиночная цифра в широкой пустой зоне читается плохо.
Числа не вышло, а явные знаки в зоне есть (полоса сукна на краю зоны не
дала срезать поля) — зона перечитывается вплотную к знакам.

Зависимости — extra [ocr]: rapidocr, onnxruntime (+ numpy, opencv-headless).
"""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from pokafer.eyes.sites import Preset, Rect

SCALE = 3          # во сколько раз увеличивается кроп: наши цифры высотой 9–13 px
BORDER = 10        # рамка цвета фона, px: моделю нужен воздух вокруг строки
THREADS = 2        # ядра ONNX Runtime: на крошечных кропах больше потоков — только медленнее
MIN_SCORE = 0.6    # уверенность модели ниже — число «под сомнением» (state = low)
BG_TOL = 14        # фон зоны узнаётся по медианной яркости (±BG_TOL)
INK_DIFF = 40      # отличие яркости от фона зоны сильнее — «чернила»
MIN_INK = 2        # меньше «чернил» в строке/столбце — шум, не текст
STRONG_INK = 110   # контраст «настоящих» чернил; полоса сукна за табличкой даёт до ~45
TIGHT_MARGIN = 2   # пустое поле вокруг числа после обрезки, px
CACHE_SIZE = 512   # сколько разных картинок зон помнит читатель: состояния стола повторяются

# число: цифры с разделителями внутри и, возможно, суффиксом порядка
# (k/K — тысячи, M/m — миллионы; для русских румов — к/К и м/М)
_AMOUNT = re.compile(r"(?<![\w.])(\d+(?:[.,\u00a0\u202f ]\d+)*)"
                    r"(?:\s*([kKmMкКмМ])(?![\w@]))?")   # суффикс — в конце слова
_GROUP_SEPARATORS = str.maketrans({",": None, "\u00a0": None, "\u202f": None, " ": None})
_SPACES = str.maketrans({".": None, "\u00a0": None, "\u202f": None, " ": None})
_MULTIPLIERS = {"k": 1e3, "m": 1e6, "к": 1e3, "м": 1e6}  # тыс./k, млн/m


@dataclass(frozen=True)
class NumberReading:
    state: str                     # number | low | none | empty
    value: float | None = None     # число; для low — тоже (под сомнением)
    text: str = ""                 # как прочитано, до разбора формата
    score: float = 0.0             # уверенность модели, 0..1


def contrast(image: Image.Image) -> int:
    """Насколько сильно зона отличается от своего фона (медиана по зоне): 0 — пусто."""
    gray = np.asarray(image.convert("L"), dtype=np.int16)
    return int(np.abs(gray - np.median(gray)).max()) if gray.size else 0


def tighten(image: Image.Image, margin: int = TIGHT_MARGIN, ink_diff: int = INK_DIFF) -> Image.Image:
    """Обрезать пустые поля зоны: оставить то, что отличается от фона, ± margin.

    ink_diff — какой контраст считать чернилами: INK_DIFF — обычное чтение;
    STRONG_INK — только явные знаки (полоса сукна на краю зоны уже не чернила).
    """
    gray = np.asarray(image.convert("L"), dtype=np.int16)[1:-1, 1:-1]   # без рамки в 1 px
    if gray.size == 0:
        return image
    ink = np.abs(gray - int(np.median(gray))) > ink_diff
    cols = np.flatnonzero(ink.sum(axis=0) >= MIN_INK)
    rows = np.flatnonzero(ink.sum(axis=1) >= MIN_INK)
    if cols.size == 0 or rows.size == 0:
        return image                       # текста нет: пусть решает распознаватель
    w, h = image.size
    return image.crop((max(int(cols[0]) + 1 - margin, 0), max(int(rows[0]) + 1 - margin, 0),
                       min(int(cols[-1]) + 2 + margin, w), min(int(rows[-1]) + 2 + margin, h)))


def parse_amount(text: str, decimal: str = ".") -> float | None:
    """Текст зоны -> сумма или None (числа нет: «Чек», «All-in», пусто).

    decimal — разделитель дроби на сайте; другой из «., » и неразрывные
    пробелы читаются как разделители групп («1,234.56» и «1.234,56»).
    Суффикс порядка умножает: «12.5k» -> 12500. Знаки валюты и прочий текст
    вокруг числа игнорируются; если в зоне несколько чисел — берётся самое
    длинное.
    """
    matches = list(_AMOUNT.finditer(text))
    if not matches:
        return None
    # подпись рядом распознаватель может прочесть с цифрами («6aHk: 19.5») —
    # берём самое длинное число; при равенстве — последнее
    m = max(matches, key=lambda m: (len(m.group(1)), m.start()))
    digits, suffix = m.group(1), m.group(2)
    if decimal == ",":
        digits = digits.translate(_SPACES).replace(",", ".")   # точки — группы, запятая — дробь
    else:
        digits = digits.translate(_GROUP_SEPARATORS)          # запятые и пробелы — группы
    value = float(digits)
    if suffix:
        value *= _MULTIPLIERS[suffix.lower()]
    return round(value, 2)


class OcrEngine:
    """Распознаватель строк (RapidOCR: PP-OCRv5 мобильная, ONNX Runtime).

    Модель лежит в комплекте библиотеки и грузится один раз на процесс.
    read() принимает кроп-строку и возвращает (текст, уверенность).
    """

    def __init__(self, threads: int = THREADS, scale: int = SCALE,
                 min_score: float = MIN_SCORE, model_dir: Path | None = None):
        try:
            from rapidocr import EngineType, LangRec, ModelType, OCRVersion, RapidOCR
        except ImportError as e:  # зависимости распознавания — отдельная extra
            raise ImportError("распознавание чисел требует pokafer[ocr]: "
                              f'pip install -e ".[ocr]" ({e})') from e
        params = {"Rec.engine_type": EngineType.ONNXRUNTIME, "Rec.lang_type": LangRec.CH,
                  "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE,
                  "EngineConfig.onnxruntime.intra_op_num_threads": threads,
                  "Global.log_level": "error"}
        if model_dir is not None:       # модели берутся из локальной папки, без сети
            params["Global.model_root_dir"] = str(model_dir)
        self._engine = RapidOCR(params=params)
        self.scale = scale
        self.min_score = min_score

    def read(self, image: Image.Image) -> tuple[str, float]:
        """Кроп-строка -> (текст, уверенность 0..1); пустой текст — строки нет."""
        bg = _median_color(image)            # фон зоны — и рамка вокруг строки
        image = tighten(image)
        big = ImageOps.expand(image.resize((image.width * self.scale, image.height * self.scale),
                                           Image.LANCZOS), border=BORDER, fill=bg)
        res = self._engine(big, use_det=False, use_cls=False, use_rec=True)
        txts = getattr(res, "txts", None)
        if txts is None or len(txts) == 0:
            return "", 0.0
        return "".join(str(t) for t in txts), float(min(res.scores))


def _pixels(image: Image.Image) -> list:
    """Пиксели канала списком (getdata в Pillow 14 устарел)."""
    data = image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata()
    return list(data)


def _median_color(image: Image.Image) -> tuple[int, int, int]:
    """Типичный цвет зоны (медиана по каналам) — рамка и фон совпадают с ним."""
    out = []
    for ch in image.convert("RGB").split():
        values = sorted(_pixels(ch))
        out.append(int(values[len(values) // 2]))
    return tuple(out)


def _median(values) -> float:
    values = sorted(values)
    n = len(values)
    return float(values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2)


class NumberReader:
    """Читатель чисел по пресету: read(кадр, зона) -> NumberReading."""

    def __init__(self, preset: Preset, window: tuple[int, int, int, int] = (0, 0, 0, 0),
                 engine: OcrEngine | None = None):
        zones = preset.zones.get("numbers", {})
        if not zones:
            raise ValueError(f"в пресете {preset.name!r} нет зон чисел ([numbers]) — "
                             "перекалибруйте стол")
        self.preset = preset
        self.window = window
        self.engine = engine or OcrEngine()
        self.bg_lum: dict[str, float] = {path: float(lum) for path, lum in zones.items()}
        self.decimal: str = preset.zones.get("numbers_format", {}).get("decimal", ".")
        # ответ модели зависит только от пикселей зоны: не изменившаяся зона не
        # читается заново (между кадрами меняются одна-две зоны из четырнадцати)
        self._memo: OrderedDict[bytes, NumberReading] = OrderedDict()

    def paths(self) -> list[str]:
        """Зоны чисел пресета (по алфавиту)."""
        return sorted(self.bg_lum)

    def zone(self, path: str, window: tuple[int, int, int, int] | None = None) -> Rect:
        """Прямоугольник зоны числа в пикселях окна."""
        return self.preset.zone(path, window or self.window)

    def read(self, frame: Image.Image, path: str,
             window: tuple[int, int, int, int] | None = None) -> NumberReading:
        """Число зоны path ('hero.stack', 'seat.pos_top.bet', ...).

        empty — элемента нет: фон зоны не тот (например, фишка ставки не
        показана); none — элемент есть, числа нет («Чек», «All-in»); low —
        число прочитано неуверенно.
        """
        if path not in self.bg_lum:
            raise KeyError(f"зона числа {path!r} не описана в пресете ([numbers])")
        x, y, w, h = self.zone(path, window)
        crop = frame.crop((x, y, x + w, y + h))
        if abs(_median(_pixels(crop.convert("L"))) - self.bg_lum[path]) > BG_TOL:
            return NumberReading("empty")
        pixels = crop.tobytes()
        reading = self._memo.get(pixels)
        if reading is not None:
            self._memo.move_to_end(pixels)
            return reading
        text, score = self.engine.read(crop)
        value = parse_amount(text, self.decimal)
        if value is None and contrast(crop) > STRONG_INK:
            # числа не вышло, а явные знаки в зоне есть: один знак в широкой
            # зоне (сукно на её краю не даёт срезать поля) модель читает плохо —
            # стек олл-ина «0» выходил «。». Перечитать вплотную к знакам.
            text, score = self.engine.read(tighten(crop, ink_diff=STRONG_INK))
            value = parse_amount(text, self.decimal)
        if value is None:
            reading = NumberReading("none", text=text, score=score)
        else:
            confident = score >= self.engine.min_score
            reading = NumberReading("number" if confident else "low", value, text, score)
        self._memo[pixels] = reading
        while len(self._memo) > CACHE_SIZE:            # давние состояния уходят первыми
            self._memo.popitem(last=False)
        return reading


__all__ = ["BG_TOL", "MIN_SCORE", "NumberReading", "NumberReader", "OcrEngine",
           "contrast", "parse_amount", "tighten"]
