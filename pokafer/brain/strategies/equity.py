"""Equity-стратегия (уровень 1 лестницы): решения из вычисленных вероятностей.

Вместо hand-констант «ставь с силой 0.6» здесь считаются числа:
  - Монте-Карло-эквити руки против смоделированного диапазона оппонента
    (pokafer/brain/ranges.py: позиция, его стек в bb, линия раздачи,
    накопленный VPIP — если платформа передала профили);
  - пот-оддсы каждого решения;
  - SPR (стек/банк) — глубина, определяющая сайзинги и коммит.

Смешанная стратегия: вероятности — плавные функции запаса эквити над
оддсами, плюс джиттер порогов (непредсказуемость живёт в данных, не в if).
Параметры — немногочисленные принципиальные пороги, для исследований
меняются в PARAMS. Всё локально, файлы не пишет, число игроков — из Snapshot.
"""
from __future__ import annotations

import random
from typing import ClassVar

from pokafer.brain.ranges import (
    position_fraction,
    preflop_order,
    ranges_for_opponents,
)
from pokafer.brain.strategies.base import Strategy, normalize
from pokafer.core.abstraction import load_ranking, preflop_class
from pokafer.core.cards import parse_card
from pokafer.core.equity import equity_exact_river, equity_mc
from pokafer.core.legal import LegalAction
from pokafer.core.schema import SeatState, Snapshot

PARAMS: dict = {
    # Монте-Карло
    "rollouts": 700,
    "time_budget_ms": 90.0,
    # постфлоп
    "call_margin": 0.035,      # запас эквити над пот-оддсами для колла
    "value_thr": 0.60,         # порог велью-ставки на свободной игре
    "raise_thr": 0.72,         # порог рейза против ставки
    "multiway_strict": 0.055,  # насколько строже пороги на каждого живого оппонента
    "commit_spr": 1.6,         # SPR ниже этого играем на стек при эквити > commit_eq
    "commit_eq": 0.52,
    "fvb_bluff": 0.58,         # fold_vs_bet соперника, с которого включаются блефы
    "bluff_cap": 0.30,
    "barrel_decay": 0.75,      # повторная агрессия этой же руки пассивнее
    # префлоп
    "open_w_utg": 0.16,
    "open_w_btn": 0.48,
    "hu_open_w": 0.55,         # HU: кнопка/SB открывает долю рук
    "option_raise_w": 0.16,    # доля рук для рейза на BB-опции
    "defend_bonus": 0.10,      # защита блайндов против позднего стила шире
    "call_odds_k": 1.9,        # пот-оддсы -> доля рук для колла рейза
    "call_odds_base": 0.16,
    "threebet_w": 0.09,        # доля рук для 3-бета на первый рейз
    "jam_bb": 13.0,            # эффективный стек ниже — короткие пуш-решения
    "jam_w": 0.22,             # доля рук для пуша коротким стеком
    "open_size_bb": 2.6,       # размер открытия в bb (плюс позиционная надбавка)
    "open_size_pos_k": 0.6,
    "jitter": 0.04,
}


def _smooth(p: float) -> float:
    """Вероятность, зажатая в [0, 1] (плавность даёт порог + джиттер)."""
    return min(1.0, max(0.0, p))


def _closest_bet(legal: list[LegalAction], target: float) -> LegalAction | None:
    """Легальный bet/raise с суммой «до», ближайшей к целевой (без олл-ина)."""
    bets = [la for la in legal if la.action in ("bet", "raise")]
    if not bets:
        return None
    menu = bets[:-1] if len(bets) > 1 else bets  # олл-ин — только явно
    return min(menu, key=lambda la: abs((la.amount_to or 0.0) - target))


def _all_in(legal: list[LegalAction]) -> LegalAction | None:
    bets = [la for la in legal if la.action in ("bet", "raise")]
    return bets[-1] if bets else None


def _parse_cards(cards: list[str]) -> list[int]:
    return [parse_card(s) for s in cards]


class _Context:
    """Одноразовый разбор Snapshot: всё, что нужно веткам решений."""

    def __init__(self, snap: Snapshot, legal: list[LegalAction],
                 profiles: dict[str, dict] | None):
        self.snap = snap
        self.legal = legal
        self.note = ""
        self.by_key = {la.key: la for la in legal}
        self.profiles = profiles or {}
        self.me: SeatState = next(
            s for s in snap.seats if s.seat == snap.actor_seat
        )
        self.opponents = [s for s in snap.seats
                          if s.seat != self.me.seat and not s.folded]
        self.max_bet = max(
            (s.bet_street for s in snap.seats if not s.folded), default=0.0
        )
        call_la = next((la for la in legal if la.action == "call"), None)
        self.call_key = call_la.key if call_la else None
        self.to_call = max(
            0.0, (call_la.amount_to or 0.0) - self.me.bet_street
        ) if call_la else 0.0
        self.odds = (
            self.to_call / (snap.pot + self.to_call)
            if self.to_call > 1e-9 else 0.0
        )
        self.hole = _parse_cards(self.me.hole_cards or [])
        self.board = _parse_cards(snap.board)
        self.my_percentile = (
            load_ranking().percentile(preflop_class(self.hole))
            if len(self.hole) == 2 else 1.0
        )
        self.pos_frac = position_fraction(snap, self.me.seat)
        self.me_is_blind = self.me.seat in preflop_order(snap)[-2:]
        opp_stacks = [o.stack for o in self.opponents] or [self.me.stack]
        self.eff_stack_bb = min(self.me.stack, max(opp_stacks)) / snap.bb
        self.raiser_profile = self._raiser_profile()
        self.raiser_pos_frac = self._raiser_pos_frac()

    def _raiser_profile(self) -> dict | None:
        """Профиль того, кто рейзил префлоп (для лёгких 3-бетов)."""
        for a in self.snap.actions:
            if a.street == "preflop" and a.type in ("bet", "raise"):
                name = next(
                    (s.name for s in self.snap.seats if s.seat == a.seat), None
                )
                return self.profiles.get(name)
        return None

    def _raiser_pos_frac(self) -> float:
        for a in self.snap.actions:
            if a.street == "preflop" and a.type in ("bet", "raise"):
                return position_fraction(self.snap, a.seat)
        return 0.0


class EquityStrategy(Strategy):
    name: ClassVar[str] = "equity"
    label: ClassVar[str] = "эквити-машина (MC vs диапазону, пот-оддсы, SPR)"

    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self._last_note = ""

    # ---------- интерфейс стратегии ----------

    def distribution(
        self, snap: Snapshot, legal: list[LegalAction],
        profiles: dict[str, dict] | None = None,
    ) -> dict[str, float]:
        ctx = _Context(snap, legal, profiles)
        if snap.street == "preflop":
            dist = self._preflop(ctx)
        else:
            dist = self._postflop(ctx)
        dist = {k: v for k, v in dist.items() if k in ctx.by_key and v > 1e-9}
        if not dist:
            dist = {"check" if "check" in ctx.by_key else
                    (ctx.call_key or legal[0].key): 1.0}
        self._last_note = ctx.note
        return normalize(dist)

    def explain(self, snap, legal, dist) -> str:
        top = max(dist.items(), key=lambda kv: kv[1])
        return f"{self._last_note}; топ-действие {top[0]} ({top[1]:.0%})"

    # ---------- префлоп ----------

    def _preflop(self, c: _Context) -> dict[str, float]:
        p = PARAMS
        pct = min(max(c.my_percentile + self.rng.uniform(-p["jitter"], p["jitter"]), 0.0), 1.0)
        raises = [a for a in c.snap.actions
                  if a.street == "preflop" and a.type in ("bet", "raise")]

        if c.eff_stack_bb <= p["jam_bb"] and c.to_call > 1e-9:
            return self._preflop_short_stack_vs_raise(c, pct)
        if c.to_call <= 1e-9:
            return self._preflop_option(c, pct)
        if not raises:  # нас не рейзили: открытие / лимп / фолд
            return self._preflop_open(c, pct)
        return self._preflop_facing_raise(c, pct, len(raises))

    def _preflop_option(self, c: _Context, pct: float) -> dict[str, float]:
        """Своя игра (BB-опция): рейз с сильных, чек остальных."""
        p = PARAMS
        raise_p = _smooth((p["option_raise_w"] - pct) / 0.10)
        dist: dict[str, float] = {}
        la = _closest_bet(c.legal, c.max_bet + 3.0 * c.snap.bb)
        if la is not None:
            dist[la.key] = raise_p
        dist["check"] = 1.0 - dist.get(la.key, 0.0) if la else 1.0
        c.note = f"BB-опция: рука {c.my_percentile:.0%} percentile"
        return dist

    def _preflop_open(self, c: _Context, pct: float) -> dict[str, float]:
        """Открытие: доля рук по позиции, сайзинг по позиции и стеку."""
        p = PARAMS
        n = len(c.snap.seats)
        if n == 2:
            open_share = p["hu_open_w"] + (0.10 if c.me.seat != c.snap.button_seat else 0)
        else:
            open_share = p["open_w_utg"] + (p["open_w_btn"] - p["open_w_utg"]) * c.pos_frac
        if c.eff_stack_bb <= p["jam_bb"]:
            open_share += 0.10
        open_share = min(open_share, 1.0)

        target = c.max_bet + (
            p["open_size_bb"] + p["open_size_pos_k"] * c.pos_frac
        ) * c.snap.bb
        open_la = _closest_bet(c.legal, target)
        if open_la is None:
            c.note = f"открытие невозможно (меню без ставок), рука {c.my_percentile:.0%}"
            return {c.call_key: 1.0} if c.call_key else {"fold": 1.0}

        raise_p = _smooth((open_share - pct) / 0.06)
        call_p = _smooth((open_share * 1.8 - pct) / 0.12) * (1.0 - raise_p) * 0.55
        dist = {open_la.key: raise_p}
        if c.call_key:
            dist[c.call_key] = call_p
        if "fold" in c.by_key:
            dist["fold"] = max(1.0 - raise_p - call_p, 1e-6)
        c.note = (
            f"открытие с позиции {c.pos_frac:.0%}: диапазон {open_share:.0%}, "
            f"рука {c.my_percentile:.0%} percentile"
        )
        return dist

    def _preflop_facing_raise(self, c: _Context, pct: float, n_raises: int) -> dict[str, float]:
        """Против рейза: 3-бет с сильных/по статистике, колл по оддсам, фолд."""
        p = PARAMS
        steal = 1.0 if c.raiser_pos_frac >= 0.66 and c.me_is_blind else 0.0
        call_thr = min(
            0.92,
            p["call_odds_base"] + c.odds * p["call_odds_k"]
            + steal * p["defend_bonus"] + 0.05 * (n_raises - 1),
        )
        threebet_w = p["threebet_w"] / max(1, n_raises)
        light_3bet = 0.0
        prof = c.raiser_profile
        if prof and prof.get("hands", 0) >= 5:
            fvb = float(prof.get("fold_vs_bet", 0.5))
            light_3bet = _smooth((fvb - 0.60) / 0.20) * 0.12

        threebet_p = min(
            _smooth((threebet_w - pct) / 0.05) + light_3bet * _smooth((0.45 - pct) / 0.20),
            0.85,
        )
        call_p = _smooth((call_thr - pct) / 0.10) * (1.0 - threebet_p)
        fold_p = max(1.0 - threebet_p - call_p, 1e-6)

        dist: dict[str, float] = {}
        if threebet_p > 1e-9:
            target = c.max_bet * 3.0 - c.me.bet_street * 2.0 + c.snap.pot * 0.2
            la = _closest_bet(c.legal, target)
            if la is not None:
                dist[la.key] = threebet_p
            else:
                call_p += threebet_p
        if c.call_key:
            dist[c.call_key] = call_p
        if "fold" in c.by_key:
            dist["fold"] = fold_p
        prof_note = f", VPIP рейзера {prof['vpip']:.0%}" if prof and prof.get("hands") else ""
        c.note = f"против {n_raises} рейза: percentile {c.my_percentile:.0%}, оддсы {c.odds:.0%}{prof_note}"
        return dist

    def _preflop_short_stack_vs_raise(self, c: _Context, pct: float) -> dict[str, float]:
        """Короткий стек против рейза: пуш с приличных рук, иначе фолд/колл-олл-ин."""
        p = PARAMS
        jam_w = max(p["jam_w"], min(0.92, p["call_odds_base"] + c.odds * p["call_odds_k"]))
        la = _all_in(c.legal)
        if la is None:
            return {c.call_key: 1.0} if c.call_key else {"fold": 1.0}
        if pct <= jam_w + self.rng.uniform(-0.02, 0.02):
            c.note = (
                f"пуш {c.eff_stack_bb:.0f}bb против рейза: percentile {c.my_percentile:.0%}, "
                f"оддсы {c.odds:.0%}"
            )
            return {la.key: 1.0}
        call_p = 0.12 if c.call_key else 0.0
        dist: dict[str, float] = {}
        if c.call_key:
            dist[c.call_key] = call_p
        if "fold" in c.by_key:
            dist["fold"] = max(1.0 - call_p, 1e-6)
        c.note = f"короткий стек {c.eff_stack_bb:.0f}bb: рука {c.my_percentile:.0%}, фолд"
        return dist

    # ---------- постфлоп ----------

    def _postflop(self, c: _Context) -> dict[str, float]:
        p = PARAMS
        n_opp = len(c.opponents)
        strict = p["multiway_strict"] * max(0, n_opp - 1)

        eq, res = self._equity(c)
        value_thr = p["value_thr"] + strict
        raise_thr = p["raise_thr"] + strict
        margin = p["call_margin"] + strict * 0.5
        spr = c.eff_stack_bb * c.snap.bb / max(c.snap.pot, 1e-9)
        fvb_avg = self._fold_vs_bet_avg(c)
        barrels = sum(
            1 for a in c.snap.actions
            if a.seat == c.me.seat and a.street != "preflop"
            and a.type in ("bet", "raise")
        )
        aggr_decay = p["barrel_decay"] ** barrels

        if c.to_call > 1e-9:
            return self._facing_bet(c, eq, odds=c.odds, spr=spr,
                                    value_thr=raise_thr, margin=margin,
                                    fvb_avg=fvb_avg, aggr_decay=aggr_decay,
                                    res=res, n_opp=n_opp)
        return self._free_play(c, eq, spr=spr, value_thr=value_thr,
                               fvb_avg=fvb_avg, aggr_decay=aggr_decay,
                               res=res, n_opp=n_opp)

    def _equity(self, c: _Context):
        """Эквити против диапазонов живых оппонентов (ривер при 1 оппоненте — точно)."""
        opp_ranges = ranges_for_opponents(c.snap, c.me.seat, c.profiles)
        samplers = [r.sampler(self.rng) for r in opp_ranges.values()]
        if len(c.board) == 5 and len(samplers) == 1:
            combos = next(iter(opp_ranges.values())).combos(set(c.hole) | set(c.board))
            res = equity_exact_river(c.hole, c.board, combos)
            return res.equity, res
        res = equity_mc(
            c.hole, c.board, opponent_samplers=samplers,
            n_rollouts=PARAMS["rollouts"], rng=self.rng,
            time_budget_ms=PARAMS["time_budget_ms"],
        )
        return res.equity, res

    def _fold_vs_bet_avg(self, c: _Context) -> float | None:
        """Средний fold_vs_bet живых оппонентов (если статистика передана)."""
        if not c.profiles:
            return None
        fvbs = [
            float(pr.get("fold_vs_bet", 0.5))
            for name, pr in c.profiles.items()
            if pr.get("hands", 0) >= 5
            and any(o.name == name for o in c.opponents)
        ]
        return sum(fvbs) / len(fvbs) if fvbs else None

    def _facing_bet(self, c: _Context, eq: float, odds: float, spr: float,
                    value_thr: float, margin: float, fvb_avg: float | None,
                    aggr_decay: float, res, n_opp: int) -> dict[str, float]:
        """Против ставки: колл по оддсам, рейз с велью, блеф-рейз по статистике."""
        p = PARAMS
        if spr < p["commit_spr"] and eq >= p["commit_eq"]:
            la = _all_in(c.legal)
            if la is not None:
                c.note = (
                    f"коммит на стек: эквити {eq:.0%} vs оддсы {odds:.0%}, SPR {spr:.1f}, "
                    f"n={res.n}"
                )
                return {la.key: 1.0}

        call_p = _smooth((eq - odds - margin) / 0.06)
        raise_p = _smooth((eq - value_thr) / 0.08) * (1.0 - call_p * 0.5)
        bluff_r = 0.0
        if fvb_avg is not None and eq < 0.45:
            bluff_r = (
                _smooth((fvb_avg - p["fvb_bluff"] - 0.06) / 0.15) * 0.18 * aggr_decay
            )
        fold_p = max(1.0 - call_p - raise_p - bluff_r, 1e-6)

        dist: dict[str, float] = {}
        if raise_p + bluff_r > 1e-9:
            target = c.max_bet + c.snap.pot * (0.9 if eq >= value_thr else 0.55)
            la = _closest_bet(c.legal, target)
            if la is not None:
                dist[la.key] = raise_p + bluff_r
            else:
                call_p += raise_p + bluff_r
        if c.call_key:
            dist[c.call_key] = call_p
        if "fold" in c.by_key:
            dist["fold"] = fold_p
        fvb_note = f", fold-vs-бет {fvb_avg:.0%}" if fvb_avg is not None else ""
        c.note = (
            f"эквити {eq:.0%} vs {n_opp} диапазон(ов), оддсы {odds:.0%}{fvb_note}"
            + (f", n={res.n} ({'точный' if res.exact else 'MC'})" if res.n else "")
        )
        return dist

    def _free_play(self, c: _Context, eq: float, spr: float, value_thr: float,
                   fvb_avg: float | None, aggr_decay: float, res,
                   n_opp: int) -> dict[str, float]:
        """Своя игра: велью-ставка / блеф по статистике / чек."""
        p = PARAMS
        bet_p = _smooth((eq - value_thr) / 0.07)
        bluff_p = 0.0
        if fvb_avg is not None and eq < value_thr:
            bluff_p = (
                _smooth((fvb_avg - p["fvb_bluff"]) / 0.18) * p["bluff_cap"] * aggr_decay
            )
        bet_total = min(bet_p + bluff_p, 0.95)

        dist: dict[str, float] = {}
        if bet_total > 1e-9:
            frac = 0.5 + (eq - value_thr) * 0.9
            if spr < p["commit_spr"] * 2:
                frac *= 1.3
            if bluff_p > bet_p:
                frac = 0.55
            target = c.max_bet + c.snap.pot * max(0.33, min(1.5, frac))
            la = _closest_bet(c.legal, target)
            if la is not None:
                dist[la.key] = bet_total
        if "check" in c.by_key:
            dist["check"] = 1.0 - dist.get(la.key, 0.0) if dist else 1.0
        fvb_note = f", fold-vs-бет {fvb_avg:.0%}" if fvb_avg is not None else ""
        c.note = (
            f"эквити {eq:.0%} vs {n_opp} диапазон(ов), банк {c.snap.pot:g}, SPR {spr:.1f}"
            f"{fvb_note}"
            + (f", n={res.n}" if res.n else "")
        )
        return dist
