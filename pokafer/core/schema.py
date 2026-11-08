"""Каноническая схема состояния игры (Snapshot) и решения (Decision).

Snapshot — ЕДИНСТВЕННЫЙ формат входа для «мозга». Любой источник данных
(исторический лог, живой стол, внешний API) обязан привести состояние к
этому виду — тогда мозгу всё равно, откуда пришли данные.

Все суммы в «чипах» (или больших блайндах — выбор за адаптером), float.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Street = Literal["preflop", "flop", "turn", "river", "showdown"]

# post = блайнды/анте; raise покрывает и bet, и raise (amount_to)
ActionType = Literal["post", "fold", "check", "call", "bet", "raise"]

DecisionAction = Literal["fold", "check", "call", "bet", "raise"]


class ActionRecord(BaseModel):
    """Одно действие в истории раздачи."""

    order: int  # порядковый номер с начала раздачи (0-...)
    street: Street
    seat: int
    type: ActionType
    # amount_to — «до» для bet/raise (сумма на улице), amount — сколько добавлено чипов
    amount_to: Optional[float] = None
    amount: float = 0.0

    def key(self) -> str:
        if self.type in ("bet", "raise"):
            return f"{self.type}:{self.amount_to:g}"
        return self.type


class SeatState(BaseModel):
    """Состояние места за столом на момент снапшота."""

    seat: int
    name: str
    stack: float  # за игроком, за вычетом ставок этой улицы
    bet_street: float = 0.0  # вложено на текущей улице
    invested: float = 0.0  # вложено за раздачу всего
    folded: bool = False
    all_in: bool = False
    is_hero: bool = False  # чья это перспектива (чьи карты видны)
    hole_cards: Optional[list[str]] = None  # ['As', 'Kd'] — только если известны


class Snapshot(BaseModel):
    """Состояние раздачи с точки зрения одного игрока (actor)."""

    schema_version: int = 1
    hand_id: str
    table_id: str = "default"
    variant: str = "nlhe"

    sb: float
    bb: float
    ante: float = 0.0

    button_seat: int
    actor_seat: int  # кто должен действовать (перспектива снапшота)

    street: Street
    board: list[str] = Field(default_factory=list)
    pot: float  # банк = сумма всех вложений всех игроков

    seats: list[SeatState]
    actions: list[ActionRecord] = Field(default_factory=list)


class Decision(BaseModel):
    """Решение «мозга» по Snapshot."""

    action: DecisionAction
    amount_to: Optional[float] = None  # для bet/raise — итоговая сумма на улице
    distribution: dict[str, float] = Field(default_factory=dict)  # смешанная стратегия
    strategy: str = ""
    reasoning: Optional[str] = None
    memory_version: Optional[str] = None
    elapsed_ms: float = 0.0

    def key(self) -> str:
        """Единый формат ключа действия: 'fold', 'check', 'call:2', 'raise:6'."""
        if self.amount_to is not None:
            return f"{self.action}:{self.amount_to:g}"
        return self.action
