"""Реплей исторических снапшотов из JSONL — офлайн-режим для мозга.

Формат строки: {"snapshot": {...}} — ровно то, что пишет scripts/demo_cli.py
с флагом --record, и то, что будут производить конвертеры реальных румов.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from pokafer.core.schema import Snapshot


class ReplayFile:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def snapshots(self) -> Iterator[Snapshot]:
        with self.path.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                    if "snapshot" in payload:
                        payload = payload["snapshot"]
                    yield Snapshot.model_validate(payload)
                except Exception as e:  # noqa: BLE001 — логируем и идём дальше
                    raise ValueError(f"{self.path}:{line_no}: битая строка: {e}") from e

    @staticmethod
    def append(path: str | Path, snap: Snapshot, decision: dict | None = None) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"snapshot": snap.model_dump()}
        if decision is not None:
            row["decision"] = decision
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
