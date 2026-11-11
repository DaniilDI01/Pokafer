#!/usr/bin/env python3
"""Совместимая обёртка: логика в pokafer/benchmark.py, конфиг в configs/benchmark.py.

Предпочтительный запуск — через CLI: pokafer bench --hands 30000
"""
from pokafer.benchmark import main

if __name__ == "__main__":
    main()
