#!/usr/bin/env python3
"""Запуск HTTP-сервиса мозга.

    python scripts/serve_brain.py --port 8000 --strategy heuristic
    docs: http://127.0.0.1:8000/docs
"""
import argparse

import uvicorn

from pokafer.brain.service import create_app


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--strategy", default="heuristic")
    args = p.parse_args()
    uvicorn.run(create_app(args.strategy), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
