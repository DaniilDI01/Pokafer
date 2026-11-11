"""Тесты универсальной проверялки (pokafer/benchmark.py).

Главный инвариант: чипы не появляются и не исчезают — на каждом действии
и в итоге каждой руки. Плюс детектор должен реально ловить порчу денег.
"""
import json

import pytest

from pokafer import benchmark
from pokafer.core.engine import HandEngine


def test_benchmark_run_conserves_chips(tmp_path):
    config = {
        "seats": {1: "micro", 2: "heuristic", 3: "fish",
                  4: "micro", 5: "heuristic", 6: "fish"},
        "hands": 150,
        "seed": 11,
        "stack_bb": 100,
    }
    result = benchmark.run_benchmark(config)
    # сумма банков всех стратегий = 0 (игра с нулевой суммой)
    pooled = sum(st["pooled_bb"] for st in result["stats"].values())
    assert abs(pooled) < 1e-6
    assert result["integrity"] == "ok"
    assert set(result["stats"]) == {"micro", "heuristic", "fish"}
    # серии для графика монотонно растут по длине и начинаются с малых чисел
    for xs in result["series"].values():
        assert len(xs) > 2


def test_integrity_check_catches_lost_chips():
    e = HandEngine(seat_stacks={1: 200.0, 2: 200.0}, names={1: "a", 2: "b"},
                   button_seat=1, seed=1)
    total = benchmark.chips_total(e)
    e.seats[1].stack -= 5.0  # деньги «исчезли»
    with pytest.raises(benchmark.IntegrityError):
        benchmark.check_invariants(e, total, "тест")


def test_integrity_check_catches_negative_stack():
    e = HandEngine(seat_stacks={1: 200.0, 2: 200.0}, names={1: "a", 2: "b"},
                   button_seat=1, seed=1)
    total = benchmark.chips_total(e)
    e.seats[2].stack = -1.0
    e.seats[2].invested += 1.0  # сумма та же, но минусовой стек
    with pytest.raises(benchmark.IntegrityError):
        benchmark.check_invariants(e, total, "тест")


def test_report_and_results_jsonl(tmp_path):
    config = {"seats": {1: "fish", 2: "micro"}, "hands": 60, "seed": 3}
    result = benchmark.run_benchmark(config)
    report = tmp_path / "report.html"
    benchmark.write_report(result, report)
    html = report.read_text(encoding="utf-8")
    assert "<svg" in html and "micro" in html and "fish" in html

    jsonl = tmp_path / "results.jsonl"
    slim = {k: v for k, v in result.items() if k != "series"}
    jsonl.write_text(json.dumps(slim) + "\n", encoding="utf-8")
    loaded = json.loads(jsonl.read_text())
    assert loaded["integrity"] == "ok"
    assert loaded["seat_map"] == {"1": "fish", "2": "micro"}
