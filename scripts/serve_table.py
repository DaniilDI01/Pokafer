#!/usr/bin/env python3
"""Запуск веб-стола (человек против мозга в браузере).

    python scripts/serve_table.py --port 8010 --strategy heuristic
    открыть http://127.0.0.1:8010
"""
import argparse

import uvicorn

from pokafer.web.table_server import create_table_app


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8010)
    p.add_argument("--strategy", default="heuristic")
    p.add_argument("--bot-think", type=float, default=0.5, dest="bot_think",
                   help="пауза между ходами ботов (сек; больше = легче калибровке глаз)")
    args = p.parse_args()
    uvicorn.run(create_table_app(args.strategy, bot_think_sec=args.bot_think),
                host=args.host, port=args.port)


if __name__ == "__main__":
    main()
