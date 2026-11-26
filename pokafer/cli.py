"""Pokafer CLI — единая точка управления проектом.

    pokafer                  — список команд
    pokafer status           — что запущено, стратегии, где логи и отчёты
    pokafer stop             — остановить все процессы pokafer
    pokafer <команда> ...    — любой инструмент проекта (флаги как у скриптов)
"""
from __future__ import annotations

import argparse
import json
import os
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
    "eyes-demo": (
        "scripts/eyes_demo.py",
        "зрение в реальном времени: кадр страницы -> карты, числа, индикаторы -> трекер -> зеркало",
        "pokafer eyes-demo --url http://127.0.0.1:8010",
    ),
    "fight": (
        "scripts/fight.py",
        "бой: герой играет за столом только глазами — кадры → трекер → мозг → клик",
        "pokafer fight --url http://127.0.0.1:8010 --strategy equity --hands 100",
    ),
    "eyes-view": (
        "scripts/eyes_view.py",
        "зеркало глаз: локальная страница «что видят глаза» (отдельный процесс)",
        "pokafer eyes-view --port 8020",
    ),
}


# локальные сервисы проекта: что проверяют status и stop
SERVICES = (
    ("HTTP-API мозга", 8000, "/health"),
    ("веб-стол      ", 8010, "/v1/strategies"),
    ("зеркало глаз  ", 8020, "/api/health"),
)


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


# маркеры командной строки процессов, поднятых pokafer (см. cmd_stop)
_CMD_MARKERS = (
    "pokafer bench", "pokafer arena", "pokafer demo",
    "pokafer table", "pokafer brain", "pokafer eyes", "pokafer fight", "pokafer.benchmark",
)
_SCRIPT_MARKERS = (
    "serve_table.py", "serve_brain.py",
    "scripts/benchmark.py", "scripts/arena.py", "scripts/replay_offline.py",
    "scripts/eyes_demo.py", "scripts/eyes_view.py", "pokafer.eyes.viewer",
    "scripts/test_flags_accuracy.py", "scripts/test_tracker_accuracy.py",
    "scripts/test_window_states.py", "scripts/test_card_accuracy.py",
    "scripts/test_numbers_accuracy.py", "scripts/test_fight_accuracy.py",
    "scripts/calibrate_pokafer.py",
)


def _ancestor_pids(pid: int) -> set[int]:
    """Сам процесс и вся цепочка родителей.

    Командная строка оболочки вызывающего содержит текст его же команды
    («pokafer stop; python x.py»): без этого исключения stop убивал терминал,
    из которого его запустили.
    """
    chain = {pid}
    while pid > 1:
        try:
            status = (Path("/proc") / str(pid) / "status").read_text()
            pid = int(next(line for line in status.splitlines()
                           if line.startswith("PPid:")).split()[1])
        except (OSError, StopIteration, ValueError):
            break
        chain.add(pid)
    return chain


def _pokafer_processes() -> list[tuple[int, str]]:
    """PID и командная строка процессов, поднятых pokafer. Чужие не попадают.

    Критерии (строгие, чтобы не задеть ничего постороннего):
      - в cmdline есть маркер команды pokafer («pokafer bench» и т.п.), ИЛИ
      - в cmdline есть маркер скрипта проекта И процесс работает из репозитория
        (cwd внутри Pokafer/);
      - процесс — python; свой процесс (сам `pokafer stop`) исключён.
    """
    my_pid = os.getpid()
    ancestors = _ancestor_pids(my_pid)   # терминал/оболочка, откуда нас вызвали
    out: list[tuple[int, str]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid <= 1 or pid in ancestors:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
            cmdline = raw.replace(b"\x00", b" ").decode("utf-8", "ignore")
            if "python" not in cmdline:
                continue
            cwd = (entry / "cwd").resolve()
        except OSError:
            continue  # процесс уже умер или недоступен
        by_command = any(m in cmdline for m in _CMD_MARKERS)
        by_script = any(m in cmdline for m in _SCRIPT_MARKERS) and cwd.is_relative_to(ROOT)
        if by_command or by_script:
            out.append((pid, cmdline.strip()))
    return out


def cmd_stop() -> int:
    """Остановить всё, что поднял pokafer: стол, мозг, фоновые прогоны."""
    import signal
    import time

    procs = _pokafer_processes()
    if not procs:
        print("pokafer: запущенных процессов не найдено")
    for pid, cmd in procs:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"  SIGTERM -> {pid}: {cmd[:100]}")
        except ProcessLookupError:
            print(f"  {pid}: уже завершён")
    if procs:
        time.sleep(0.7)
        for pid, _ in procs:
            if Path(f"/proc/{pid}").exists():
                try:
                    os.kill(pid, signal.SIGKILL)
                    print(f"  SIGKILL -> {pid} (не отреагировал на SIGTERM)")
                except ProcessLookupError:
                    pass
    for name, port, probe in SERVICES:
        code = _http_code(f"http://127.0.0.1:{port}{probe}")
        print(f"  {name} :{port} — " + ("ВСЁ ЕЩЁ РАБОТАЕТ" if code else "остановлен"))
    return 0


def cmd_status() -> int:
    from pokafer.brain.strategies import available

    print("Pokafer — статус")
    strategies = available()
    print("  стратегии:", "  ".join(f"{k} ({v})" for k, v in strategies.items()))
    for name, port, probe in SERVICES:
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
        "  stop     остановить все процессы pokafer (стол, мозг, прогоны)\n" + \
        "\nУ каждой команды своя справка: pokafer <команда> -h\n" + \
        "Конфиг бенчмарка: configs/benchmark.py"

    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("status", help="что запущено, стратегии, где логи и отчёты")
    sub.add_parser("stop", help="остановить все процессы pokafer (стол, мозг, прогоны)")
    for name, (_, desc, _) in COMMANDS.items():
        # add_help=False: -h пробрасывается скрипту (справка в одном месте)
        sub.add_parser(name, help=desc, add_help=False)

    args, rest = p.parse_known_args(argv)
    if not args.cmd:
        p.print_help()
        return 0
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "stop":
        return cmd_stop()
    # всё после имени команды уходит скрипту как есть
    run_script(COMMANDS[args.cmd][0], argv[1:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
