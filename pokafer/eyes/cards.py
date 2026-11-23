"""Распознавание карт по шаблонам пресета: целая карта, чернильная метрика.

Рендер детерминирован: одна и та же карта на том же месте при той же
калибровке даёт пиксель-в-пиксель тот же кроп. Метрика — доля отличий
по ЧЕРНИЛАМ (пикселям глифов): одинаковые карты ~0, разные 10%+; порог
0.06 разделяет с запасом. Кропы сравниваются только с шаблонами своего
размера; шаблоны и запросы обязаны происходить от ОДНОЙ калибровки
(смена калибровки -> пересбор шаблонов).

Сбор: scripts/collect_card_templates.py (наш стол — с ground truth;
чужой сайт — кластеризация + разметка, см. PLAN_EYES.md).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

DIFF_TRESH = 25    # пиксель «отличается», если канал разошёлся сильнее
BG_LEVEL = 215     # светлее этого — фон карты (не «чернила»)
ACCEPT_FRAC = 0.06  # порог доли отличий для принятия совпадения


def diff_fraction(a: Image.Image, b: Image.Image) -> float:
    """Доля отличий по ЧЕРНИЛАМ (нормировка на пиксели глифов)."""
    if a.size != b.size:
        return 1.0
    pa, pb = a.convert("RGB").tobytes(), b.convert("RGB").tobytes()
    ink = diff = 0
    for i in range(0, len(pa), 3):
        a_ink = pa[i] < BG_LEVEL or pa[i + 1] < BG_LEVEL or pa[i + 2] < BG_LEVEL
        b_ink = pb[i] < BG_LEVEL or pb[i + 1] < BG_LEVEL or pb[i + 2] < BG_LEVEL
        if not (a_ink or b_ink):
            continue  # фон обеих — не сравниваем и не считаем
        ink += 1
        if (abs(pa[i] - pb[i]) > DIFF_TRESH
                or abs(pa[i + 1] - pb[i + 1]) > DIFF_TRESH
                or abs(pa[i + 2] - pb[i + 2]) > DIFF_TRESH):
            diff += 1
    return diff / ink if ink else 0.0


class CardTemplates:
    """Шаблоны: {метка: [кропы разных размеров]} по пресету."""

    def __init__(self, preset_name: str, templates_dir: Path | None = None):
        base = (templates_dir or Path("data/eyes/templates")) / preset_name
        self.variants: dict[str, list[Image.Image]] = {}
        for f in sorted(base.glob("card_*.png")):
            label = f.stem.removeprefix("card_").rsplit("_", 1)[0]
            self.variants.setdefault(label, []).append(Image.open(f).convert("RGB"))
        self.back = Image.open(base / "back.png").convert("RGB") \
            if (base / "back.png").exists() else None
        self._sizes = {t.size for imgs in self.variants.values() for t in imgs}

    @property
    def ready(self) -> bool:
        return bool(self.variants)

    def labels(self) -> list[str]:
        return sorted(self.variants)

    def match(self, crop: Image.Image) -> tuple[str | None, float]:
        """Кроп -> (метка, доля отличий); None, если лучшее совпадение хуже порога."""
        crop = crop.convert("RGB")
        if crop.size not in self._sizes:
            return None, 1.0
        best_label, best = None, 1.0
        for label, imgs in self.variants.items():
            for tpl in imgs:
                if tpl.size != crop.size:
                    continue  # классы размеров не смешиваются
                f = diff_fraction(crop, tpl)
                if f < best:
                    best, best_label = f, label
        if best <= ACCEPT_FRAC:
            return best_label, best
        return None, best

    def is_back(self, crop: Image.Image) -> bool:
        """Рубашка (закрытая карта бота)?"""
        if self.back is None or self.back.size != crop.size:
            return False
        return diff_fraction(crop, self.back) <= ACCEPT_FRAC


# JS для калибровочных скриптов: карты, отрисованные клиентом СЕЙЧАС.
# Рантайм глаз это НЕ использует (DOM запрещён); нужно коллектору шаблонов
# и тесту точности для сверки свежести рендера с правдой сервера.
DOM_CARDS_JS = """
    const SUITS = {'\u2660': 's', '\u2665': 'h', '\u2666': 'd', '\u2663': 'c'};
    const cardOf = (el) => {
      const r = el.querySelector('.r'); const s = el.querySelector('.s');
      if (!r || !s) return null;
      return r.textContent + (SUITS[s.textContent] || '?');
    };
    const heroEls = [...document.querySelectorAll('.seat.hero .cards .card')];
    const boardEls = [...document.querySelectorAll('#board .card')];
    return {hero: heroEls.map(cardOf), board: boardEls.map(cardOf)};
"""
