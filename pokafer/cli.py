"""Pokafer CLI — единая точка управления проектом.

    pokafer                  — список команд
    pokafer status           — что запущено, стратегии, где логи и отчёты
    pokafer <команда> ...    — любой инструмент проекта (флаги как у скриптов)
"""
from __future__ import annotations

import argparse
import json
import runpy
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# имя -> (скрипт, описание, пример запуска)
COMMANDS = {
    "demo": (
        "scripts/demo_cli.py",
        "сыграть руку против мозга в терминале",
        "pokafer demo --strategy micro",
    ),
    "table": (
        "scripts/serve_table.py",
        "веб-стол в браузере: вы против ботов с разными стратегиями",
        "pokafer table --port 8010",
    ),
    "brain": (
        "scripts/serve_brain.py",
        "HTTP-API мозга: JSON на входе -> решение (docs на /docs)",
        "pokafer brain --port 8000",
    ),
    "bench": (
        "scripts/benchmark.py",
        "обкатка стратегий: рассадка в configs/benchmark.py, тысячи раздач, "
        "график банков, контроль целостности чипов",
        "pokafer bench --hands 30000",
    ),
    "arena": (
        "scripts/arena.py",
        "дуэль двух стратегий 1-на-1, bb/100 с доверительным интервалом",
        "pokafer arena --a micro --b fish --hands 100000",
    ),
    "replay": (
        "scripts/replay_offline.py",
        "прогон мозга по историческим снапшотам (JSONL) + профили игроков",
        "pokafer replay data/snapshots/demo.jsonl",
    ),
}


def run_script(rel: str, rest: list[str]) -> None:
    """Прямой проброс в скрипт: у скрипта свои флаги и своя -h."""
    path = ROOT / rel
    saved = sys.argv
    sys.argv = [str(path)] + rest
    try:
        runpy.run_path(str(path), run_name="__main__")
    finally:
        sys.argv = saved


def _http_code(url: str) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=1.5) as r:
            return r.status
    except Exception:
        return None


def cmd_status() -> int:
    from pokafer.brain.strategies import available

    print("Pokafer — статус")
    strategies = available()
    print("  стратегии:", "  ".join(f"{k} ({v})" for k, v in strategies.items()))
    for name, port, probe in (
        ("HTTP-API мозга", 8000, "/health"),
        ("веб-стол      ", 8010, "/v1/strategies"),
    ):
        code = _http_code(f"http://127.0.0.1:{port}{probe}")
        print(f"  {name} :{port} — " + (f"запущен (HTTP {code})" if code else "не запущен"))
    logs = ROOT / "data" / "logs"
    print(f"  логи и отчёты: {logs}")
    results = logs / "results.jsonl"
    if results.exists():
        lines = [ln for ln in results.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            print("  последние прогоны бенчмарка (свежие сверху):")
            for ln in reversed(lines[-3:]):
                r = json.loads(ln)
                wr = ", ".join(
                    f"{s} {st['winrate_bb100']:+.0f}"
                    for s, st in r["stats"].items()
                )
                print(f"    {r['ts']} · {r['hands']} рук · {wr} bb/100 · "
                      f"целостность: {r['integrity']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    p = argparse.ArgumentParser(
        prog="pokafer",
        description="Pokafer — покерный ИИ: один CLI для всех инструментов.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.epilog = "Команды:\n" + "\n".join(
        f"  {name:8s} {desc}\n          пример: {ex}"
        for name, (_, desc, ex) in COMMANDS.items()
    ) + "\n  status   что запущено, доступные стратегии, последние прогоны\n" + \
        "\nУ каждой команды своя справка: pokafer <команда> -h\n" + \
        "Конфиг бенчмарка: configs/benchmark.py"

    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("status", help="что запущено, стратегии, где логи и отчёты")
    for name, (_, desc, _) in COMMANDS.items():
        # add_help=False: -h пробрасывается скрипту (справка в одном месте)
        sub.add_parser(name, help=desc, add_help=False)

    args, rest = p.parse_known_args(argv)
    if not args.cmd:
        p.print_help()
        return 0
    if args.cmd == "status":
        return cmd_status()
    # всё после имени команды уходит скрипту как есть
    run_script(COMMANDS[args.cmd][0], argv[1:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
