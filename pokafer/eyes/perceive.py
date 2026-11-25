"""Что глаза видят в кадре: слоты карт и зоны чисел — чистая функция кадра.

Ядро рантайма: на вход кадр окна и читатели пресета, на выход Perception —
всё распознанное, с прямоугольниками и уверенностью. Трекер собирает из
последовательности Perception состояние раздачи, зеркало показывает то же
самое на странице отладки. DOM здесь нет, состояния между кадрами нет.
"""
from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass

from PIL import Image

from pokafer.eyes.cards import CardReader, Reading
from pokafer.eyes.numbers import NumberReading, NumberReader
from pokafer.eyes.sites import Rect
from pokafer.eyes.slots import CardSlot, card_slots

Window = tuple[int, int, int, int]


@dataclass(frozen=True)
class CardSeen:
    slot: CardSlot
    reading: Reading


@dataclass(frozen=True)
class SeatCards:
    """Что с картами места: hidden | revealed | none | unclear."""

    state: str
    cards: list[str] = dataclasses.field(default_factory=list)   # для revealed


@dataclass(frozen=True)
class Perception:
    """Всё, что глаза увидели в одном кадре."""

    seq: int
    frame: Image.Image
    cards: list[CardSeen]
    numbers: dict[str, NumberReading]          # зона числа -> прочтение
    number_rects: dict[str, Rect]               # зона числа -> где она в кадре
    seats: dict[str, SeatCards]                 # место -> состояние его карт
    timings: dict[str, float]                   # мс на этап: cards, numbers, total

    def to_state(self) -> dict:
        """Состояние без кадра — JSON для зеркала (страница отладки) и журналов."""
        w, h = self.frame.size
        return {
            "seq": self.seq, "size": [w, h], "timings": self.timings,
            "cards": [{"path": c.slot.path, "rect": list(c.slot.rect), "kind": c.slot.kind,
                       "state": c.reading.state, "label": c.reading.label,
                       "nearest": c.reading.nearest, "score": round(c.reading.score, 4)}
                      for c in self.cards],
            "numbers": [{"path": path, "rect": list(self.number_rects[path]),
                         "state": r.state, "value": r.value, "text": r.text,
                         "score": round(r.score, 3)}
                        for path, r in sorted(self.numbers.items())],
            "seats": [{"seat": seat, "state": sc.state, "cards": sc.cards}
                      for seat, sc in sorted(self.seats.items())],
        }


def perceive(frame: Image.Image, card_reader: CardReader, number_reader: NumberReader,
             seq: int = 0, window: Window = (0, 0, 0, 0)) -> Perception:
    """Прочитать один кадр: все слоты карт и зоны чисел пресета."""
    t0 = time.perf_counter()
    cards = [CardSeen(slot, card_reader.read(frame, slot.rect, slot=slot.path))
             for slot in card_slots(card_reader.preset, window)]
    t1 = time.perf_counter()
    numbers: dict[str, NumberReading] = {}
    rects: dict[str, Rect] = {}
    for path in number_reader.paths():
        rects[path] = number_reader.zone(path, window)
        numbers[path] = number_reader.read(frame, path, window)
    seats = _seat_cards(cards)
    t2 = time.perf_counter()
    return Perception(seq, frame, cards, numbers, rects, seats,
                      {"cards": round((t1 - t0) * 1000, 2),
                       "numbers": round((t2 - t1) * 1000, 2),
                       "total": round((t2 - t0) * 1000, 2)})


def _seat_cards(cards: list[CardSeen]) -> dict[str, SeatCards]:
    """Сводка по картам каждого места из сырых чтений его слотов.

    У места два слота открытых карт (58x82) и два слота рубашек (34x48,
    лежат внутри первых). Пока карты скрыты, слоты открытых карт содержат
    рубашки и сукно — по отдельности это «не распознато», но вместе с
    рубашками картина однозначная. Раскрываются карты всегда слева направо,
    поэтому пустой первый слот открытых карт и пустой первый слот рубашек
    означают, что карт у места нет.
    """
    by_path = {c.slot.path: c.reading for c in cards if c.slot.kind == "seat"}
    out: dict[str, SeatCards] = {}
    for path in by_path:
        seat = path.split(".", 2)[1]
        if seat in out:
            continue
        c0, c1 = by_path.get(f"seat.{seat}.cards[0]"), by_path.get(f"seat.{seat}.cards[1]")
        b0, b1 = by_path.get(f"seat.{seat}.backs[0]"), by_path.get(f"seat.{seat}.backs[1]")
        def is_(reading, state: str) -> bool:
            return reading is not None and reading.state == state

        faces = sum(1 for r in (c0, c1) if is_(r, "face"))
        if faces == 2:                              # рубашки под вскрытыми картами не смотрим
            out[seat] = SeatCards("revealed", [c0.label, c1.label])
        elif faces:                                # карта и рубашка (или сукно) одновременно
            out[seat] = SeatCards("unclear")
        elif is_(b0, "back") and is_(b1, "back"):
            out[seat] = SeatCards("hidden")
        elif is_(c0, "empty") and is_(b0, "empty"):
            out[seat] = SeatCards("none")
        else:
            out[seat] = SeatCards("unclear")
    return out
