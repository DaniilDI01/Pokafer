"""Vanilla CFR на покере Куна — минимальная «живая» реализация ядра GTO-блюпринта.

Почему это важно для проекта: CFR (Counterfactual Regret Minimization) —
алгоритм, которым получены Cepheus/Libratus/Pluribus. Покер Куна (3 карты,
максимум 1 ставка) — наименьшая игра, где уже есть блеф: равновесие Нэша
обязывает P1 блефовать валетом с частотой ~1/3. То есть «блеф» не хардкод —
он ВЫЧИСЛЯЕТСЯ как свойство равновесия. Этот модуль — доказательство
концепта и шаблон для будущего MCCFR-блюпринта на NLHE.

Реализация без зависимостей; exploitability считается точным best-response.
"""
from __future__ import annotations

import random
from itertools import permutations, product as _product

ACTIONS = ("p", "b")  # p = пас/чек/фолд, b = ставка/колл
CARDS = (1, 2, 3)  # J, Q, K

_TERMINAL_PAYOFF_P1 = {
    "pp": lambda c: 1 if c[0] > c[1] else -1,
    "pbp": lambda c: -1,
    "pbb": lambda c: 2 if c[0] > c[1] else -2,
    "bp": lambda c: 1,
    "bb": lambda c: 2 if c[0] > c[1] else -2,
}
_CARD_NAME = {1: "J", 2: "Q", 3: "K"}


def _to_act(history: str) -> int:
    """0 = P1 (нечётная длина истории), 1 = P2."""
    return len(history) % 2


def _is_terminal(history: str) -> bool:
    return history in _TERMINAL_PAYOFF_P1


def _payoff_p1(history: str, cards: tuple[int, int]) -> int:
    return _TERMINAL_PAYOFF_P1[history](cards)


class KuhnCFR:
    def __init__(self, iterations: int = 0, seed: int | None = None):
        self.rng = random.Random(seed)
        # regrets[player][(card, history)][action]
        self.regrets: list[dict] = [{}, {}]
        # суммы стратегий для среднего (итогового) профиля
        self.strat_sum: list[dict] = [{}, {}]
        self._iterations = 0
        if iterations:
            self.train(iterations)

    # ---------- обучение ----------

    def train(self, iterations: int) -> None:
        deals = list(permutations(CARDS, 2))
        for _ in range(iterations):
            for cards in deals:
                self._cfr("", cards, 1.0 / len(deals), 1.0 / len(deals))
        self._iterations += iterations

    def _cfr(self, history: str, cards: tuple[int, int], p0: float, p1: float) -> float:
        """Возвращает значение узла для ДЕЙСТВУЮЩЕГО игрока (минус для родителя)."""
        if _is_terminal(history):
            # Значение узла — для «действующего» игрока на нём (противник
            # последнего действовавшего); родитель инвертирует знак.
            u = _payoff_p1(history, cards)
            return u if _to_act(history) == 0 else -u
        player = _to_act(history)
        info = (cards[player], history)
        sigma = self._regret_matching(player, info)

        vals = {}
        for a in ACTIONS:
            child_p0, child_p1 = (p0 * sigma[a], p1) if player == 0 else (p0, p1 * sigma[a])
            vals[a] = -self._cfr(history + a, cards, child_p0, child_p1)
        node_val = sum(sigma[a] * vals[a] for a in ACTIONS)

        reg = self.regrets[player].setdefault(info, {"p": 0.0, "b": 0.0})
        opp_reach = p1 if player == 0 else p0
        for a in ACTIONS:
            reg[a] += opp_reach * (vals[a] - node_val)
        ssum = self.strat_sum[player].setdefault(info, {"p": 0.0, "b": 0.0})
        own_reach = p0 if player == 0 else p1
        for a in ACTIONS:
            ssum[a] += own_reach * sigma[a]
        return node_val

    def _regret_matching(self, player: int, info: tuple[int, str]) -> dict[str, float]:
        reg = self.regrets[player].get(info)
        if not reg or max(reg.values()) <= 0:
            return {"p": 0.5, "b": 0.5}
        pos = {a: max(0.0, reg[a]) for a in ACTIONS}
        total = sum(pos.values())
        return {a: pos[a] / total for a in ACTIONS}

    # ---------- результаты ----------

    def average_strategy(self, player: int) -> dict[str, dict[str, float]]:
        """Итоговая смешанная стратегия: {'J': {'p': 0.67, 'b': 0.33}, ...}."""
        out: dict[str, dict[str, float]] = {}
        for (card, history), ssum in self.strat_sum[player].items():
            total = ssum["p"] + ssum["b"]
            if total <= 0:
                continue
            key = f"{_CARD_NAME[card]}|{history or 'root'}"
            out[key] = {"p": ssum["p"] / total, "b": ssum["b"] / total}
        return dict(sorted(out.items()))

    def _avg_sigma(self, player: int, info: tuple[int, str]) -> dict[str, float]:
        ssum = self.strat_sum[player].get(info)
        if not ssum or ssum["p"] + ssum["b"] <= 0:
            return {"p": 0.5, "b": 0.5}
        total = ssum["p"] + ssum["b"]
        return {"p": ssum["p"] / total, "b": ssum["b"] / total}

    def exploitability(self) -> float:
        """BR(P1) + BR(P2) >= 0, ноль только в равновесии.

        Best response считается ТОЧНО перебором чистых стратегий по
        информационным множествам BR-игрока (их 6 -> 64 варианта):
        максимум линейного функционала достигается в чистой стратегии.
        """
        return (self._br_value(0) + self._br_value(1)) / 6.0

    @staticmethod
    def _infoset_keys(player: int) -> list[tuple[int, str]]:
        if player == 0:
            return [(c, h) for c in CARDS for h in ("", "pb")]
        return [(c, h) for c in CARDS for h in ("p", "b")]

    def _br_value(self, br_player: int) -> float:
        keys = self._infoset_keys(br_player)
        best = -1e18
        for combo in _product(ACTIONS, repeat=len(keys)):
            policy = dict(zip(keys, combo))
            best = max(best, self._ev(br_player, policy))
        return best

    def _ev(self, br_player: int, policy: dict) -> float:
        """Сумма (по 6 раздачам) выигрыша BR-стратегии против средней стратегии."""
        return sum(
            self._ev_walk("", br_player, policy, cards)
            for cards in permutations(CARDS, 2)
        )

    def _ev_walk(
        self, history: str, br_player: int, policy: dict, cards: tuple[int, int]
    ) -> float:
        if _is_terminal(history):
            u = _payoff_p1(history, cards)
            return u if br_player == 0 else -u
        actor = _to_act(history)
        if actor == br_player:
            a = policy.get((cards[actor], history), "p")
            return self._ev_walk(history + a, br_player, policy, cards)
        sigma = self._avg_sigma(actor, (cards[actor], history))
        return sum(
            sigma[a] * self._ev_walk(history + a, br_player, policy, cards)
            for a in ACTIONS
        )
