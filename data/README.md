# Данные

Поток: `raw` → `reference` (ручные решения) → `prepared` → `mart` → `dashboard`. Файлы в `raw` вручную не правятся, исправления делаются кодом или записываются в `reference`.

## raw — исходные файлы и ответы API

- `resorts.json` — справочник и признаки 32 курортов (первая строка — названия полей)
- `prices_monthly.json` — цены ски-пассов из исходного архива
- `prices_manual_2026.csv` — цены, собранные вручную с сайтов курортов, со ссылкой и датой проверки
- `rosstat_tourist_trips.csv`, `rosstat_tourist_nights.csv` — поездки и ночёвки по регионам (ЕМИСС)
- `Turpotok_1kv-2026.xlsx` — полная выгрузка Росстата: годовой, квартальный и месячный ряды
- `climate_monthly.json` — месячные климатические показатели
- `resort_open_data.json` — исходный сбор API (V1, 14.09.2026)
- `resort_open_data_v2.json` — новый сбор по проверенным координатам (V2, 26.10.2026)

## reference — ручные решения и справочники

- `coordinate_review.xlsx` → `coordinate_overrides.csv` — ручная проверка координат и статус работы курорта
- `coordinate_disputes_decisions.csv` — решения по точкам, где Википедия расходится с ручной проверкой
- `region_dictionary.csv` — соответствие названий регионов
- `region_manual_check.csv`, `growth_check_3_regions.csv` — сверка рядов Росстата
- `regional_tourism_sources.json` — каталог региональных источников

## prepared, processed, mart, dashboard

- `prepared/` — промежуточные таблицы из ноутбуков (регион × год, флаги, сравнение сборов)
- `processed/model_data.json` — контрольный расчёт из исходного архива; эталон для теста `test_matches_control_result`
- `mart/` — `resort_features.csv`, `resort_scores.csv`, сценарии, интервалы, группы
- `dashboard/` — таблицы для DataLens (см. `docs/steps/step7.md`)
- `overlap_pairs.csv` — пары курортов с пересечением радиусов 6 км
- `coordinate_review.csv` — шаблон ручной проверки координат, созданный в шаге 1
