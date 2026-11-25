"""Слоты карт пресета: какие прямоугольники кадра читать как карты.

Перечисление чисто геометрическое — по узлам пресета; какой слот что должен
показывать, решает трекер. Порядок устойчив: герой, борд, места по алфавиту.
"""
from __future__ import annotations

from dataclasses import dataclass

from pokafer.eyes.sites import Preset, Rect


@dataclass(frozen=True)
class CardSlot:
    path: str    # 'hero.cards[1]', 'board.slots[3]', 'seat.pos-tl.backs[0]'
    rect: Rect
    kind: str    # hero | board | seat


def seat_names(preset: Preset) -> list[str]:
    """Имена мест пресета (узлы [seat.<имя>]), по алфавиту."""
    seats = preset.zones.get("seat", {})
    return sorted(seats) if isinstance(seats, dict) else []


def card_slots(preset: Preset, window: tuple[int, int, int, int]) -> list[CardSlot]:
    """Все слоты карт стола в пикселях окна."""
    out: list[CardSlot] = []

    def add(path: str, kind: str) -> None:
        for i, rect in enumerate(preset.slots(path, window)):
            out.append(CardSlot(f"{path}[{i}]", rect, kind))

    add("hero.cards", "hero")
    add("board.slots", "board")
    for seat in seat_names(preset):
        add(f"seat.{seat}.backs", "seat")
        add(f"seat.{seat}.cards", "seat")
    return out


def slot_rect(preset: Preset, path: str, window: tuple[int, int, int, int]) -> Rect:
    """Прямоугольник одного слота по его пути: 'board.slots[2]'."""
    name, _, index = path.partition("[")
    try:
        i = int(index.rstrip("]"))
    except ValueError as e:
        raise KeyError(f"путь слота {path!r}: ожидался вид 'узел[индекс]'") from e
    rects = preset.slots(name, window)
    if not 0 <= i < len(rects):
        raise KeyError(f"слот {path!r}: в узле {name!r} слотов {len(rects)}")
    return rects[i]
