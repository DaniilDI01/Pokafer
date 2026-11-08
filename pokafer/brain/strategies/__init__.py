"""Реестр стратегий: мозг выбирает их по имени (строка в конфиге/запросе)."""
from __future__ import annotations

from pokafer.brain.strategies.base import Strategy
from pokafer.brain.strategies.fish import FishStrategy
from pokafer.brain.strategies.heuristic import HeuristicStrategy
from pokafer.brain.strategies.micro import MicroExploitStrategy
from pokafer.brain.strategies.random_strategy import RandomStrategy

_REGISTRY: dict[str, type[Strategy]] = {}
_INSTANCES: dict[str, Strategy] = {}


def register(cls: type[Strategy]) -> type[Strategy]:
    _REGISTRY[cls.name] = cls
    return cls


def available() -> dict[str, str]:
    return {name: cls.label for name, cls in _REGISTRY.items()}


def get(name: str, **kwargs) -> Strategy:
    if name not in _REGISTRY:
        raise KeyError(f"Неизвестная стратегия {name!r}. Доступны: {sorted(_REGISTRY)}")
    key = f"{name}:{sorted(kwargs.items())}"
    if key not in _INSTANCES:
        _INSTANCES[key] = _REGISTRY[name](**kwargs)
    return _INSTANCES[key]


for _cls in (RandomStrategy, HeuristicStrategy, FishStrategy, MicroExploitStrategy):
    register(_cls)
