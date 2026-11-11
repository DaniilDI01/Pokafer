"""Универсальная проверялка стратегий (логика; конфиг — configs/benchmark.py).

Запуск через CLI: pokafer bench [--hands N] [--seed S] [--out файл.html]
или напрямую: python scripts/benchmark.py (совместимая обёртка).

Что делает:
  - играет конфиг-ное число раздач: место -> стратегия, стеки на руку свежие
    (измеряем винрейт, а не выживание), кнопка равномерно по всем местам;
  - ИНТЕГРИВНОСТЬ: после КАЖДОГО действия сумма чипов (стеки+вложения)
    неизменна и стеки не отрицательны; при нарушении — аварийный дамп
    раздачи (действия + seed колоды) в data/logs/ и останов;
  - логи: data/logs/benchmark.log, сводки прогонов — data/logs/results.jsonl;
  - итог: bb/100 на место ± 95% CI и HTML-отчёт с графиком суммарного
    банка каждой стратегии.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import math
import random
import time
from pathlib import Path

from pokafer.brain.api import Brain
from pokafer.brain.strategies import available as strategies_available
from pokafer.core.engine import HandEngine

ROOT = Path(__file__).resolve().parents[1]
LOGS_DIR = ROOT / "data" / "logs"
DEFAULT_CONFIG = ROOT / "configs" / "benchmark.py"

BB = 2.0
COLORS = ["#4f8fd6", "#e05252", "#3aa76d", "#f0b429", "#9a6dd0", "#d0709a"]
Z = 1.96  # 95%

logger = logging.getLogger("pokafer.benchmark")


class IntegrityError(RuntimeError):
    """Нарушен инвариант: чипы появились/исчезли, минусовой стек и т.п."""


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    """Конфиг — Python-файл с CONFIG (переменные сверху, с комментариями)."""
    if not path.exists():
        raise FileNotFoundError(
            f"нет конфига {path}. Скопируйте пример: configs/benchmark.py "
            "(место за столом -> стратегия, hands, seed, stack_bb)"
        )
    spec = importlib.util.spec_from_file_location("benchmark_config", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cfg = mod.CONFIG
    if "seats" not in cfg:
        raise ValueError("в CONFIG нет 'seats' (место -> стратегия)")
    return cfg


# ---------- инварианты ----------

def chips_total(engine: HandEngine) -> float:
    return sum(s.stack + s.invested for s in engine.seats.values())


def check_invariants(engine: HandEngine, expected_total: float, where: str) -> None:
    total = chips_total(engine)
    if abs(total - expected_total) > 1e-6:
        raise IntegrityError(
            f"{where}: чипы изменились {expected_total!r} -> {total!r} "
            f"(расхождение {total - expected_total:+.6f})"
        )
    for s in engine.seats.values():
        if s.stack < -1e-9 or s.invested < -1e-9:
            raise IntegrityError(f"{where}: минусовой стек/вложение у {s.name}: {s}")


def dump_crash(engine: HandEngine, hand_no: int, seed_val: int, reason: str) -> Path:
    """Аварийный дамп раздачи для воспроизведения бага."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    uniq = f"{time.strftime('%H%M%S')}_{random.randrange(0x10000):04x}"
    path = LOGS_DIR / f"crash_hand{hand_no}_{uniq}.json"
    data = {
        "reason": reason,
        "hand_no": hand_no,
        "deck_seed": seed_val,
        "actions": [
            {"order": a.order, "street": a.street, "seat": a.seat,
             "type": a.type, "amount_to": a.amount_to, "amount": a.amount}
            for a in engine.actions
        ],
        "seats": [
            {"seat": s.seat, "name": s.name, "stack": s.stack,
             "invested": s.invested, "folded": s.folded, "all_in": s.all_in,
             "hole": [c for c in s.hole]}
            for s in engine.seats.values()
        ],
        "board": engine.board,
        "street": engine.street,
        "result": engine.result,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


# ---------- прогон ----------

def run_benchmark(config: dict) -> dict:
    seats_cfg: dict[int, str] = {int(k): v for k, v in config["seats"].items()}
    hands: int = int(config["hands"])
    stack = float(config.get("stack_bb", 100)) * BB
    seed = config.get("seed", 7)

    unknown = [s for s in seats_cfg.values() if s not in strategies_available()]
    if unknown:
        raise KeyError(f"неизвестные стратегии {unknown}; есть: {sorted(strategies_available())}")

    rng = random.Random(seed)
    n = len(seats_cfg)
    counters: dict[str, int] = {}
    names: dict[int, str] = {}
    for seat in sorted(seats_cfg):
        st = seats_cfg[seat]
        counters[st] = counters.get(st, 0) + 1
        names[seat] = f"{st}#{counters[st]}"
    strategies = sorted(set(seats_cfg.values()))
    seats_per = {s: 0 for s in strategies}
    for st in seats_cfg.values():
        seats_per[st] += 1

    brains = {seat: Brain(st) for seat, st in seats_cfg.items()}
    totals = {s: 0.0 for s in strategies}
    per_hand = {s: [] for s in strategies}
    total_chips = stack * n
    t0 = time.perf_counter()
    integrity_ok = True

    logger.info("старт: %d мест [%s], %d рук, seed=%s, стек %.0f bb",
                n, ", ".join(f"{s}×{seats_per[s]}" for s in strategies), hands, seed, stack / BB)

    for h in range(hands):
        button = h % n + 1  # кнопка по кругу: всем поровну, смещения нет
        deck_seed = rng.randrange(2**31)
        engine = HandEngine(
            seat_stacks={seat: stack for seat in seats_cfg},
            names=names,
            button_seat=button,
            sb=1.0, bb=BB,
            seed=deck_seed,
            hand_id=f"bench-{h:07d}",
            table_id="benchmark",
        )
        check_invariants(engine, total_chips, f"рука {h}, пост-блайнды")
        try:
            guard = 0
            while not engine.hand_over:
                guard += 1
                if guard > 500:
                    raise IntegrityError(f"рука {h}: зацикливание (>500 действий)")
                seat = engine.actor_seat
                d = brains[seat].decide(engine.snapshot_for(seat))
                engine.apply(d.action, d.amount_to)
                check_invariants(engine, total_chips,
                                 f"рука {h}, действие #{len(engine.actions)}")
        except IntegrityError as e:
            path = dump_crash(engine, h, deck_seed, str(e))
            logger.critical("ИНТЕГРИВНОСТЬ НАРУШЕНА: %s | дамп: %s", e, path)
            integrity_ok = False
            raise
        final = sum(s.stack for s in engine.seats.values())
        if abs(final - total_chips) > 1e-6:
            path = dump_crash(engine, h, deck_seed,
                              f"итог руки: {final:g} != {total_chips:g}")
            logger.critical("ИНТЕГРИВНОСТЬ НАРУШЕНА: итог руки %s | дамп: %s", final, path)
            raise IntegrityError(f"рука {h}: итоговые чипы {final:g} != {total_chips:g}")

        hand_net = {s: 0.0 for s in strategies}
        for seat in seats_cfg:
            hand_net[seats_cfg[seat]] += (engine.seats[seat].stack - stack) / BB
        for s in strategies:
            totals[s] += hand_net[s]
            per_hand[s].append(hand_net[s])

        if (h + 1) % max(1, hands // 10) == 0:
            el = time.perf_counter() - t0
            logger.info("  %d/%d рук (%.0f рук/с)", h + 1, hands, (h + 1) / el)

    dt = time.perf_counter() - t0
    stats = {}
    for s in strategies:
        n_s = seats_per[s]
        xs = per_hand[s]
        mean = sum(xs) / hands
        var = sum((x - mean) ** 2 for x in xs) / max(hands - 1, 1)
        std = math.sqrt(var)
        stats[s] = {
            "seats": n_s,
            "winrate_bb100": totals[s] / (hands * n_s) * 100.0,
            "ci95": Z * std / math.sqrt(hands) * 100.0 / n_s,
            "pooled_bb": totals[s],
        }
    result = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "hands": hands,
        "seats": n,
        "seat_map": {str(k): v for k, v in seats_cfg.items()},
        "seconds": round(dt, 2),
        "hands_per_sec": round(hands / dt, 1),
        "seed": seed,
        "integrity": "ok" if integrity_ok else "FAILED",
        "stats": stats,
        "series": {s: _downsample(per_hand[s]) for s in strategies},
    }
    logger.info("финиш: %d рук за %.1f с, целостность: %s", hands, dt,
                "ok" if integrity_ok else "FAILED")
    return result


def _downsample(xs: list[float], limit: int = 1200) -> list[float]:
    step = max(1, len(xs) // limit)
    out, acc = [], 0.0
    for i, x in enumerate(xs):
        acc += x
        if i % step == 0:
            out.append(round(acc, 2))
    out.append(round(acc, 2))
    return out


# ---------- отчёт ----------

def svg_chart(series: dict[str, list[float]], colors: dict[str, str],
              w: int = 920, h: int = 400) -> str:
    pad, bottom = 60, 46
    all_vals = [v for xs in series.values() for v in xs] or [0.0]
    lo, hi = min(all_vals), max(all_vals)
    if abs(hi - lo) < 1e-9:
        hi, lo = lo + 1, lo - 1
    span = hi - lo
    lo, hi = lo - span * 0.05, hi + span * 0.05
    max_len = max((len(xs) for xs in series.values()), default=1)

    def X(i):
        return pad + (w - 2 * pad) * (i / max(max_len - 1, 1))

    def Y(v):
        return bottom + (h - bottom - 30) * (1 - (v - lo) / (hi - lo))

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">']
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = Y(v)
        parts.append(f'<line x1="{pad}" y1="{y:.1f}" x2="{w - pad}" y2="{y:.1f}" stroke="#232a3a"/>')
        parts.append(f'<text x="{pad - 8}" y="{y + 4:.1f}" fill="#8a93a6" font-size="11" text-anchor="end">{v:.0f}</text>')
    if lo < 0 < hi:
        parts.append(f'<line x1="{pad}" y1="{Y(0):.1f}" x2="{w - pad}" y2="{Y(0):.1f}" stroke="#4a556c" stroke-width="1.6"/>')
    parts.append(f'<text x="{w // 2}" y="{h - 8}" fill="#8a93a6" font-size="12" text-anchor="middle">руки → (суммарный банк стратегии по всем её местам, bb)</text>')
    for name, xs in series.items():
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(xs))
        parts.append(f'<polyline fill="none" stroke="{colors.get(name, "#aaa")}" stroke-width="2.2" points="{pts}"/>')
    lx = pad
    for name in series:
        c = colors.get(name, "#aaa")
        parts.append(f'<rect x="{lx}" y="12" width="14" height="4" fill="{c}"/>')
        parts.append(f'<text x="{lx + 20}" y="18" fill="#e8edf4" font-size="13">{name}</text>')
        lx += 30 + 9 * len(name)
    parts.append("</svg>")
    return "".join(parts)


def write_report(result: dict, path: Path) -> None:
    colors = {s: COLORS[i % len(COLORS)] for i, s in enumerate(sorted(result["stats"]))}
    rows = "".join(
        f"<tr><td style='color:{colors[s]};font-weight:700'>{s}</td>"
        f"<td>{st['seats']}</td><td>{st['winrate_bb100']:+.1f} ± {st['ci95']:.1f}</td>"
        f"<td>{st['pooled_bb']:+.0f} bb</td></tr>"
        for s, st in sorted(result["stats"].items(), key=lambda kv: -kv[1]["winrate_bb100"])
    )
    seat_map = ", ".join(f"{k}→{v}" for k, v in sorted(result["seat_map"].items(), key=lambda kv: int(kv[0])))
    html = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><title>Pokafer — бенчмарк стратегий</title>
<style>body{{background:#10141c;color:#e8edf4;font-family:system-ui,sans-serif;margin:24px}}
table{{border-collapse:collapse;margin:16px 0}}td,th{{border:1px solid #2a3346;padding:6px 14px;text-align:right}}
th{{color:#8a93a6;font-weight:500}}td:first-child{{text-align:left}}.dim{{color:#8a93a6}}</style></head><body>
<h2>Бенчмарк стратегий</h2>
<p class="dim">{result['ts']} · {result['hands']} рук · {result['seats']} мест · {result['hands_per_sec']} рук/с ·
seed {result['seed']} · целостность: {result['integrity']}</p>
<p class="dim">рассадка: {seat_map}</p>
<table><tr><th>стратегия</th><th>мест</th><th>bb/100 на место (95% CI)</th><th>суммарный банк</th></tr>{rows}</table>
{svg_chart(result['series'], colors)}
</body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def setup_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        con = logging.StreamHandler()
        con.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(con)
        fh = logging.FileHandler(LOGS_DIR / "benchmark.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(fh)


def append_results_jsonl(result: dict) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    slim = {k: v for k, v in result.items() if k != "series"}
    with (LOGS_DIR / "results.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(slim, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="конфиг — configs/benchmark.py (место -> стратегия, hands, seed, stack_bb)",
    )
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="путь к конфигу (Python-файл с CONFIG)")
    p.add_argument("--hands", type=int, default=None, help="переопределить hands из конфига")
    p.add_argument("--seed", type=int, default=None, help="переопределить seed из конфига")
    p.add_argument("--out", default=None, help="путь HTML-отчёта")
    args = p.parse_args(argv)

    setup_logging()
    config = load_config(Path(args.config))
    if args.hands is not None:
        config["hands"] = args.hands
    if args.seed is not None:
        config["seed"] = args.seed

    result = run_benchmark(config)
    append_results_jsonl(result)

    logger.info("")
    logger.info("┌──────────────────────────────────────────────────")
    for s, st in sorted(result["stats"].items(), key=lambda kv: -kv[1]["winrate_bb100"]):
        logger.info("│ %-12s %d мест  %+7.1f ±%-5.1f bb/100  (банк %+9.0f bb)",
                    s, st["seats"], st["winrate_bb100"], st["ci95"], st["pooled_bb"])
    logger.info("└──────────────────────────────────────────────────")

    out = Path(args.out) if args.out else LOGS_DIR / f"benchmark_{time.strftime('%Y%m%d_%H%M%S')}.html"
    write_report(result, out)
    logger.info("отчёт с графиком: %s", out)
    logger.info("логи: %s | история прогонов: %s", LOGS_DIR / "benchmark.log", LOGS_DIR / "results.jsonl")


if __name__ == "__main__":
    main()
