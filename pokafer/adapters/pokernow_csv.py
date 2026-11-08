"""ПАРСЕР ЭКСПОРТОВ PokerNow.club — ЗАГОТОВКА (формат не сверен с реальным файлом).

Что известно по публичным конвертерам (pn2ps, pokerlog, OHH-конвертеры):
PokerNow отдаёт построчный CSV-лог, синтаксис близок к PokerStars:

    -- starting hand #1 (No Limit Texas Hold'em) --
    Seat 1 is the button
    Total number of players : 2/6
    Seat 1: alice ($200.00 in chips)
    Seat 2: bob ($200.00 in chips)
    alice: posts small blind $1.00
    bob: posts big blind $2.00
    *** HOLE CARDS ***
    Dealt to alice [As Kd]
    alice: raises $4.00 to $6.00
    bob: calls $4.00
    *** FLOP *** [4s 9d 2c]
    bob: checks
    alice: bets 6.00
    bob: folds
    Uncalled bet ($6.00) returned to alice
    alice collects $16.00 from pot
    --- SUMMARY --- / --- end of hand #1 ---

ПЛАН ДОРАБОТКИ (один заход, когда появится реальный файл):
  1. положить экспорт в data/raw/pokernow_<club>.csv;
  2. сверить паттерны ниже с реальными строками (комма/точка, $, форматы улиц);
  3. реализовать parse_pokernow_csv -> Iterator[Snapshot] в терминах схемы;
  4. снять эту заглушку и включить тест на реальном файле.
"""
from __future__ import annotations

from pokafer.core.schema import Snapshot


def parse_pokernow_csv(text: str) -> list[Snapshot]:
    raise NotImplementedError(
        "Парсер PokerNow ждёт реальный экспорт в data/raw/ — см. докстринг модуля."
    )
