"""Эксплойт-профиль под микролимиты (NL2–NL10): максимум винрейта против
лузово-пассивной популяции.

Логика подстройки (по литературе и данным трекеров о микролимитах):
  - блеф почти выключен: популяция не фолдит — блеф теряет деньги;
  - велью-диапазон расширен (тонкий велью-бет): нас широко коллят хуже;
  - сайзинги крупнее: колл-стайшны платят;
  - против ставок дисциплина выше: их ставка = сила, блеф-кетчи почти убраны;
  - полублеф с дро сохранён (дешёвые фолд-эквити всё же есть).
"""
from __future__ import annotations

from typing import ClassVar

from pokafer.brain.strategies.heuristic import HeuristicStrategy


class MicroExploitStrategy(HeuristicStrategy):
    name: ClassVar[str] = "micro"
    label: ClassVar[str] = "эксплойт под микролимиты (велью-тяжёлый)"

    PARAMS: ClassVar[dict] = dict(
        HeuristicStrategy.PARAMS,
        # своя игра: тоньше и крупнее велью
        bet_hi_thr=0.75, bet_hi_p=0.90,
        bet_mid_thr=0.50, bet_mid_p=0.85,   # тонкий велью-бет (нас переколлят хуже)
        bet_low_thr=0.42, bet_low_p=0.25,
        bluff_base=0.05, bluff_late=0.03,   # блеф почти выключен
        bluff_draw=0.06, bluff_cap=0.12,
        river_barrel_mult=0.5,
        size_bias=1,                        # сайзинги крупнее
        # против ставки: их ставка = сила
        raise_thr=0.85, raise_p=0.55,
        call_thr=0.62, call_p=0.70,
        marg_call_p=0.55,
        fold_p=0.90,                        # без силы фолдим: блеф-кетчи дороги
        bluffcatch_p=0.06,
        semibluff_raise_p=0.01,
    )
