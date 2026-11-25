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
pokafer                # справка: все команды проекта
pytest -q              # все тесты
```

Основные команды (у каждой своя справка `pokafer <команда> -h`):

```bash
pokafer demo                      # рука против мозга в терминале
pokafer table --port 8010         # веб-стол в браузере (вы против ботов)
pokafer brain --port 8000         # HTTP-API мозга (docs: /docs)
pokafer bench --hands 30000       # обкатка стратегий (конфиг: configs/benchmark.py)
pokafer arena --a micro --b fish  # дуэль двух стратегий, bb/100
pokafer status                    # что запущено, стратегии, последние прогоны
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

## Глаза (экран-адаптер, в разработке)

Читают покерный стол **с экрана** пикселями и собирают Snapshot для мозга.
Готово: карты и числа читаются со 100% точностью на нашем столе (в обоих
режимах захвата), есть страница «что видят глаза» для отладки. План, формат
пресета и статус — [docs/PLAN_EYES.md](docs/PLAN_EYES.md).

```bash
pip install -e ".[eyes,ocr]"                    # браузер/захват + распознавание чисел
pokafer table --port 8011 --bot-think 0.05      # стол для калибровки/тестов глаз
python scripts/calibrate_pokafer.py             # слоты карт + зоны чисел, ~70 с (1 раз на машине)
python scripts/test_card_accuracy.py            # приёмка: карты >= 99% (… --screen: пиксели с экрана)
python scripts/test_numbers_accuracy.py        # приёмка: числа >= 98% (… --screen)
pokafer eyes-demo --url http://127.0.0.1:8010   # зрение в реальном времени + зеркало
```

Числа читает готовая модель распознавания текста (PP-OCRv5, работает локально
через ONNX Runtime): новый шрифт не требует калибровки — только зоны.

## Модули

| Пакет | Что внутри |
|---|---|
| `pokafer.core` | карты и оценщик комбинаций, схема Snapshot/Decision, легальные действия, движок раздачи |
| `pokafer.brain` | Brain (JSON→JSON), реестр стратегий, память об оппонентах, FastAPI-сервис |
| `pokafer.adapters` | replay JSONL (офлайн), заготовка парсера PokerNow |
| `pokafer.web` | веб-стол: WebSocket-сервер (именованные сессии) + клиент без сборки |
| `pokafer.eyes` | глаза: захват экрана, пресеты сайтов, карты (шаблоны), числа (готовая модель), зеркало, действия |
| `pokafer.analysis` | VPIP/PFR/AF/fold-vs-bet — фичи для слоя модели оппонента |

## Стратегии: текущие и план

| Стратегия | Статус | Описание |
|---|---|---|
| `random` | ✅ | равномерная — калибровка арены |
| `heuristic` | ✅ | пот-оддсы + смешанные блефы (демо-соперник) |
| `micro` | ✅ | эксплойт под микролимиты (наследник heuristic) |
| `fish` | ✅ | синтетическая «рыба» — мишень для эксплойта |
| `equity` | ✅ | **equity-машина (уровень 1)**: MC-эквити vs диапазону, пот-оддсы, SPR, позиция, стеки, статистика соперников |
| `gto_blueprint` | 🔜 | MCCFR-блюпринт NLHE (фундамент — `core/abstraction.py` — готов) |
| `nn_value` | 🔜 | value-сеть + depth-limited solving (рецепт DeepStack) |
| `opponent_model` | 🔜 | GRU/трансформер на историях действий — эксплойт-слой |

Решения и требования проекта зафиксированы в [docs/DECISIONS.md](docs/DECISIONS.md)
(цель, гибридная схема GTO, локальность, модульность).

## Данные

- `data/snapshots/` — JSONL снапшотов: пишет `demo_cli.py --record`, читает
  `replay_offline.py`; сюда же конвертируются реальные румы;
- `data/raw/` — сырые экспорты (PokerNow CSV и др.) — их парсят адаптеры.

## Ограничения v1 (осознанные)

- анте не раздаётся (поле в схеме зарезервировано);
- парсер PokerNow ждёт реальный экспорт для сверки формата (см. докстринг
  `pokafer/adapters/pokernow_csv.py`);

Сайд-поты, сплит-поты и возврат непокрытых ставок реализованы и покрыты
тестами (`tests/test_engine.py`).

## ROADMAP

1. GTO-блюпринт: MCCFR на абстракции NLHE (OpenSpiel, CPU/кластер).
2. ~~Веб-стол: WebSocket-сервер + браузерный клиент~~ ✅ готов (`scripts/serve_table.py`).
   Глаза (экран-адаптер): карты ✅, числа ✅, зеркало ✅ → трекер → Snapshot → бой (`docs/PLAN_EYES.md`).
3. Слой оппонента: обучение на собранных снапшотах, эксплойт-контроллер.
4. GTO-оракул (TexasSolver) для метрик отклонений людей от равновесия.
5. Slumbot-адаптер для внешнего бенчмарка.
