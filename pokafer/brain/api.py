"""Brain — ядро принятия решений: Snapshot -> Decision.

Слои пайплайна:
  1. валидация Snapshot (pydantic);
  2. вывод легальных действий (pokafer.core.legal);
  3. смешанная стратегия от Strategy (меняется по имени);
  4. сэмплирование действия из распределения;
  5. Decision с распределением, пояснением и метаданными.

Мозг хранит только RNG и (опционально) память об оппонентах — состояние
раздачи приходит целиком в каждом запросе, поэтому один и тот же мозг
обслуживает и офлайн-реплей, и живой стол.
"""
from __future__ import annotations

import json
import random
import time

from pokafer.brain import strategies
from pokafer.brain.memory import InMemoryProfileStore
from pokafer.core.legal import LegalAction, legal_actions
from pokafer.core.schema import Decision, Snapshot


class Brain:
    def __init__(
        self,
        strategy: str = "heuristic",
        memory: InMemoryProfileStore | None = None,
        seed: int | None = None,
    ):
        self.rng = random.Random(seed)
        self.strategy = strategies.get(strategy, rng=random.Random(seed))
        self.memory = memory

    # ---------- наблюдение за столом (для памяти об оппонентах) ----------

    def observe(self, snap: Snapshot) -> None:
        if self.memory is not None:
            self.memory.observe(snap)

    def opponent_profiles(self, snap: Snapshot) -> dict[str, dict]:
        if self.memory is None:
            return {}
        profiles = self.memory.profiles()
        return {
            s.name: profiles[s.name]
            for s in snap.seats
            if not s.is_hero and s.name in profiles
        }

    # ---------- принятие решения ----------

    def decide(self, snap: Snapshot) -> Decision:
        t0 = time.perf_counter()
        legal = legal_actions(snap)
        dist = self.strategy.distribution(snap, legal)
        chosen = self._sample(dist, legal)

        reasoning = self.strategy.explain(snap, legal, dist)
        opp = self.opponent_profiles(snap)
        if opp:
            hints = "; ".join(
                f"{n}: VPIP {p['vpip']:.0%}" for n, p in opp.items() if p.get("hands")
            )
            if hints:
                reasoning = f"{reasoning} | память: {hints}"

        return Decision(
            action=chosen.action,
            amount_to=chosen.amount_to,
            distribution={k: round(v, 4) for k, v in dist.items()},
            strategy=self.strategy.name,
            reasoning=reasoning,
            memory_version=self.memory.version if self.memory else None,
            elapsed_ms=(time.perf_counter() - t0) * 1000.0,
        )

    def decide_json(self, payload: dict | str | bytes) -> dict:
        """JSON на входе -> JSON на выходе (главный режим для интеграций)."""
        if isinstance(payload, (str, bytes)):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("Ожидался JSON-объект снапшота")
        snap = Snapshot.model_validate(payload.get("snapshot", payload))
        decision = self.decide(snap)
        out = decision.model_dump()
        out["decision"] = decision.key()
        return out

    def _sample(self, dist: dict[str, float], legal: list[LegalAction]) -> LegalAction:
        if not dist:
            return legal[0]
        keys = list(dist)
        weights = [dist[k] for k in keys]
        key = self.rng.choices(keys, weights=weights, k=1)[0]
        for la in legal:
            if la.key == key:
                return la
        return legal[0]
