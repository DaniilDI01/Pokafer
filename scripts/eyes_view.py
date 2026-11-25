#!/usr/bin/env python3
"""Зеркало глаз — локальная страница «что видят глаза» (отдельный процесс).

    pokafer eyes-view [--port 8020]

Читает то, что публикует конвейер глаз (pokafer eyes-demo), и показывает
пользователю: http://127.0.0.1:8020/ — список открытых столов.
"""
from pokafer.eyes.viewer import main

if __name__ == "__main__":
    main()
