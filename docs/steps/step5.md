# Шаг 5: подключение нового сбора и цен, флаги качества, сценарии

Файлы, которые добавляет или заменяет архив (копировать в корень проекта):

- `config/scenarios.yaml` — сценарии расчёта (baseline, verified, verified_new_weather, verified_hourly)
- `src/resort_ranking/quality.py` — полнота данных и флаги качества (считаются кодом)
- `src/resort_ranking/overlays.py` — подключение resort_open_data_v2.json, статусов работы и ручных цен
- `src/resort_ranking/pipeline.py`, `scoring.py`, `data_io.py` — обновлены
- `tests/test_quality.py`, `tests/test_scenarios.py` — новые тесты

Запуск: `make all`.
Результаты: `data/mart/resort_scores.csv`, `resort_features.csv`, `scenario_comparison.csv`, `scenarios/*.csv`.
