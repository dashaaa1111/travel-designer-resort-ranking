# API и документация

## MediaWiki Action API

- Использование: поиск страниц курортов, координат и Wikidata ID.
- Конечная точка: `https://ru.wikipedia.org/w/api.php`
- Метод: `GET`.
- Авторизация: не требуется.
- Документация: https://www.mediawiki.org/wiki/API:Main_page
- Правила использования: https://www.mediawiki.org/wiki/API:Etiquette

## OpenStreetMap Overpass API

- Использование: подсчёт трасс, подъёмников, размещения, питания, транспорта и достопримечательностей в радиусе 6 км.
- Конечная точка: `https://overpass-api.de/api/interpreter`
- Метод: `POST` с параметром `data`, содержащим запрос Overpass QL.
- Авторизация: не требуется на публичном сервере.
- Документация: https://wiki.openstreetmap.org/wiki/Overpass_API
- Язык запросов: https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL
- Ограничение: публичный сервер рассчитан на небольшие исследования. Нужны паузы, кэш и повтор при временной ошибке.

## Open Meteo Historical Weather API

- Использование: дневная температура и снегопад по координатам курорта.
- Конечная точка: `https://archive-api.open-meteo.com/v1/archive`
- Метод: `GET`.
- Основные параметры: `latitude`, `longitude`, `start_date`, `end_date`, `daily`, `timezone`, `models`.
- Авторизация: для базового доступа ключ не требуется.
- Документация: https://open-meteo.com/en/docs/historical-weather-api

## Roschart API

- Использование: дополнительные региональные показатели из таблиц Росстата.
- Конечная точка набора: `https://roschart.ru/api/dataset/{id}`
- Метод: `GET`.
- Авторизация: часть запросов работает без ключа. Ключ передаётся через `X-API-Key` и повышает лимиты.
- Документация: https://roschart.ru/docs
- OpenAPI: https://roschart.ru/openapi.json

## Источники без API

- ЕМИСС 62157, туристские поездки: https://www.fedstat.ru/indicator/62157
- ЕМИСС 62489, ночёвки: https://www.fedstat.ru/indicator/62489
- Росстат, раздел туризма: https://rosstat.gov.ru/statistics/turizm
- OurAirports, открытые CSV: https://ourairports.com/data/

Для каждого ответа API сохраняйте дату получения, параметры запроса и исходный JSON до очистки данных.
