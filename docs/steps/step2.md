# Шаг 2: структура проекта и рефакторинг

Что добавлено (копируется в корень проекта):

- `config/weights.yaml` — веса рейтинга
- `src/resort_ranking/` — расчёт, разбитый на функции
- `tests/` — автоматические тесты
- `pytest.ini`, `Makefile` — запуск одной командой

Запуск: `make all` (расчёт + тесты).
Результаты: `data/mart/resort_scores.csv` и `data/mart/model_data.json`.
Контрольный файл `data/processed/model_data.json` не перезаписывается.
