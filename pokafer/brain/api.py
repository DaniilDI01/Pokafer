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
from collections.abc import Iterable

from pokafer.brain import strategies
from pokafer.brain.memory import InMemoryProfileStore
from pokafer.brain.strategies.base import normalize
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

    def decide(self, snap: Snapshot, profiles: dict[str, dict] | None = None,
               allowed: Iterable[str] | None = None) -> Decision:
        """Snapshot (+ опц. профили оппонентов) -> Decision.

        Мозг чистый: состояние приходит в аргументах. Если profiles не
        переданы, но платформа инъецировала память (memory), профили
        читаются из неё; хранит и собирает их — платформа, не мозг.

        allowed — типы действий, которые платформа может исполнить (например,
        история раздачи восстановлена неточно — только фолд/чек/колл).
        Стратегия считает распределение по полному меню; вес
        запрещённых отбрасывается, остаток перенормировывается. Не осталось
        ничего — чек или колл.
        """
        t0 = time.perf_counter()
        legal = legal_actions(snap)
        if profiles is None and self.memory is not None:
            profiles = self.opponent_profiles(snap) or None
        dist = self.strategy.distribution(snap, legal, profiles)
        if allowed is not None:
            dist = normalize({k: w for k, w in dist.items() if k.partition(":")[0] in allowed}) \
                or {next(la.key for la in legal if la.action in ("check", "call")): 1.0}
        chosen = self._sample(dist, legal)

        reasoning = self.strategy.explain(snap, legal, dist)
        if profiles:
            hints = "; ".join(
                f"{n}: VPIP {p['vpip']:.0%}"
                for n, p in profiles.items() if p.get("hands")
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
        """JSON на входе -> JSON на выходе (главный режим для интеграций).

        Формат: {"snapshot": {...}, "context": {"profiles": {...}}?} —
        context опционален; мозг не собирает статистику сам.
        """
        if isinstance(payload, (str, bytes)):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("Ожидался JSON-объект снапшота")
        snap = Snapshot.model_validate(payload.get("snapshot", payload))
        ctx = payload.get("context") or {}
        profiles = ctx.get("profiles")
        decision = self.decide(snap, profiles=profiles)
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
