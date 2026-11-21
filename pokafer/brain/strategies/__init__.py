"""Реестр стратегий: мозг выбирает их по имени (строка в конфиге/запросе)."""
from __future__ import annotations

from pokafer.brain.strategies.base import Strategy
from pokafer.brain.strategies.equity import EquityStrategy
from pokafer.brain.strategies.fish import FishStrategy
from pokafer.brain.strategies.heuristic import HeuristicStrategy
from pokafer.brain.strategies.micro import MicroExploitStrategy
from pokafer.brain.strategies.random_strategy import RandomStrategy

_REGISTRY: dict[str, type[Strategy]] = {}


def register(cls: type[Strategy]) -> type[Strategy]:
    _REGISTRY[cls.name] = cls
    return cls


def available() -> dict[str, str]:
    return {name: cls.label for name, cls in _REGISTRY.items()}


def get(name: str, **kwargs) -> Strategy:
    """Новый экземпляр на каждый вызов: у каждого мозга свой RNG."""
    if name not in _REGISTRY:
        raise KeyError(f"Неизвестная стратегия {name!r}. Доступны: {sorted(_REGISTRY)}")
    return _REGISTRY[name](**kwargs)


for _cls in (RandomStrategy, HeuristicStrategy, FishStrategy, MicroExploitStrategy,
             EquityStrategy):
    register(_cls)
