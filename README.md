# Pokafer

Модульный покерный ИИ для исследований покерной математики и поведения людей.
**Мозг** принимает состояние игры в JSON и возвращает решение в JSON — ему всё
равно, пришли данные из исторического лога или в реальном времени.

```
Источники (адаптеры)                     МОЗГ                          Выход
────────────────────────              ──────────────                ──────────
исторические HH (JSONL) ─→ replay ─┐                               {action,
живой стол (демо/терминал) ─────────┼→ Snapshot JSON → легальные    amount_to,
внешние API (позже: Slumbot) ───────┘   действия → стратегия        distribution,
                                        (random/heuristic/GTO/NN)   reasoning}
                                        + память об оппонентах
```

## Быстрый старт

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                                  # все тесты
python scripts/demo_cli.py                 # сыграть руку против мозга в терминале
python scripts/serve_brain.py              # HTTP-сервис на 127.0.0.1:8000
python scripts/serve_table.py              # веб-стол в браузере: http://127.0.0.1:8010
python scripts/train_kuhn.py               # CFR считает равновесие Куна (блеф включён)
python scripts/arena.py --a micro --b fish # матч стратегий, bb/100
```

## С каким JSON работает мозг

**Вход** — `Snapshot` (полное состояние раздачи с точки зрения actor'а):

```json
{
  "hand_id": "h0001", "table_id": "demo", "sb": 1, "bb": 2,
  "button_seat": 1, "actor_seat": 2, "street": "flop",
  "board": ["4s", "9d", "2c"], "pot": 12.0,
  "seats": [
    {"seat": 1, "name": "opp",  "stack": 194.0, "bet_street": 0, "invested": 6},
    {"seat": 2, "name": "hero", "stack": 194.0, "bet_street": 0, "invested": 6,
     "is_hero": true, "hole_cards": ["As", "Kh"]}
  ],
  "actions": [
    {"order": 0, "street": "preflop", "seat": 1, "type": "post", "amount_to": 1, "amount": 1},
    {"order": 1, "street": "preflop", "seat": 2, "type": "post", "amount_to": 2, "amount": 2},
    {"order": 2, "street": "preflop", "seat": 1, "type": "raise", "amount_to": 6, "amount": 5}
  ]
}
```

**Выход** — `Decision`:

```json
{
  "action": "bet", "amount_to": 6.0, "decision": "bet:6",
  "distribution": {"bet:6": 0.65, "check": 0.35},
  "strategy": "heuristic",
  "reasoning": "категория 0, сила 0.22; топ-действие bet:6 (65%)",
  "elapsed_ms": 0.4
}
```

`distribution` — смешанная стратегия: в одной и той же ситуации мозг играет
разные действия с заданными вероятностями (непредсказуемость и блеф).

## HTTP API

```bash
curl -s localhost:8000/v1/decide -H 'Content-Type: application/json' \
  -d '{"snapshot": {...}, "strategy": "heuristic", "seed": 42}'
```

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/health` | живость + версия |
| GET | `/v1/strategies` | доступные стратегии |
| GET | `/v1/schema` | JSON-схема Snapshot |
| POST | `/v1/decide` | решение по снапшоту |

## Модули

| Пакет | Что внутри |
|---|---|
| `pokafer.core` | карты и оценщик комбинаций, схема Snapshot/Decision, легальные действия, движок раздачи |
| `pokafer.brain` | Brain (JSON→JSON), реестр стратегий, память об оппонентах, FastAPI-сервис |
| `pokafer.adapters` | replay JSONL (офлайн), заготовка парсера PokerNow |
| `pokafer.analysis` | VPIP/PFR/AF/fold-vs-bet — фичи для слоя модели оппонента |
| `pokafer.research` | CFR на покере Куна (учебное ядро GTO-блюпринта) |

## Стратегии: текущие и план

| Стратегия | Статус | Описание |
|---|---|---|
| `random` | ✅ | равномерная — калибровка арены |
| `heuristic` | ✅ | пот-оддсы + смешанные блефы (демо-соперник) |
| `kuhn_cfr` | 🧪 research | CFR-равновесие Куна (ядро матчасти) |
| `gto_blueprint` | 🔜 | MCCFR-блюпринт NLHE (CPU, рецепт Pluribus) |
| `nn_value` | 🔜 | value-сеть + depth-limited solving (рецепт DeepStack) |
| `opponent_model` | 🔜 | GRU/трансформер на историях действий — эксплойт-слой |

## Данные

- `data/snapshots/` — JSONL снапшотов: пишет `demo_cli.py --record`, читает
  `replay_offline.py`; сюда же конвертируются реальные румы;
- `data/raw/` — сырые экспорты (PokerNow CSV и др.) — их парсят адаптеры.

## Ограничения v1 (осознанные)

- сайд-поты не считаются (весь банк — лучшей руке); для HU-демо не критично;
- анте не раздаётся (поле в схеме зарезервировано);
- парсер PokerNow ждёт реальный экспорт для сверки формата (см. докстринг
  `pokafer/adapters/pokernow_csv.py`).

## ROADMAP

1. GTO-блюпринт: MCCFR на абстракции NLHE (OpenSpiel, CPU/кластер).
2. ~~Веб-стол: WebSocket-сервер + браузерный клиент~~ ✅ готов (`scripts/serve_table.py`).
3. Слой оппонента: обучение на собранных снапшотах, эксплойт-контроллер.
4. GTO-оракул (TexasSolver) для метрик отклонений людей от равновесия.
5. Slumbot-адаптер для внешнего бенчмарка.
