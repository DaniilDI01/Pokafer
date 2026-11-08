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
    args = p.parse_args()
    uvicorn.run(create_table_app(args.strategy), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
