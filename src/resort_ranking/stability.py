"""Шаг 6: устойчивость рейтинга.

Что считается:
  1. Интервалы мест (Монте-Карло): веса случайно сдвигаются вокруг базовых (Dirichlet),
     курортам без цены ценовой балл берётся случайно из диапазона вместо 50.
  2. +20% к весу каждого из пяти компонентов (раздел 11 и 14 ТЗ).
  3. Leave-one-out: убираем по одному из 13 показателей, остальные веса растягиваем до 100.
  4. Границы по цене: место курорта без цены при худшей и лучшей цене.
  5. Группы приоритета по вероятности попасть в топ-K.

Модуль не пересчитывает признаки и нормировку: он берёт 13 готовых баллов из build_model()
и меняет только веса и ценовой балл курортов без цены. popularity_score и rank из витрины
не меняются, результаты пишутся в отдельные файлы data/mart/.
"""

from __future__ import annotations

import argparse
import math
import random
from pathlib import Path
from typing import Any

import yaml

from .data_io import load_scenarios, load_weights, write_csv, write_json
from .pipeline import PROJECT_ROOT, build_model
from .scoring import assign_ranks

COMPONENTS: dict[str, list[str]] = {
    "demand": ["region_trips", "region_growth", "avg_stay"],
    "infrastructure": ["pistes", "lifts", "lodging", "amenities"],
    "winter": ["snow_days", "freezing_days", "snowfall"],
    "access": ["airport", "transport"],
    "price": ["price"],
}
COMPONENT_LABELS = {
    "demand": "Региональный спрос",
    "infrastructure": "Инфраструктура",
    "winter": "Зимние условия",
    "access": "Доступность",
    "price": "Цена",
}


# ---------- конфигурация ----------

def load_stability_config(path: Path) -> dict[str, Any]:
    """Читает config/stability.yaml и проверяет основные параметры."""
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    mc = config["monte_carlo"]
    if mc["n_simulations"] < 1 or mc["concentration"] <= 0:
        raise ValueError("n_simulations >= 1 и concentration > 0")
    low, high = mc["missing_price_score"]
    if not 0 <= low <= high <= 100:
        raise ValueError("missing_price_score должен лежать в [0, 100]")
    if not 0 <= config["interval"]["low"] < config["interval"]["high"] <= 100:
        raise ValueError("interval: 0 <= low < high <= 100")
    return config


def check_components(weights: dict[str, float]) -> None:
    """Каждый показатель из весов входит ровно в один компонент."""
    listed = [key for keys in COMPONENTS.values() for key in keys]
    if sorted(listed) != sorted(weights):
        raise ValueError(f"Компоненты не совпадают с весами: {sorted(set(listed) ^ set(weights))}")


# ---------- изменение весов (сумма всегда 100) ----------

def _normalize(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("Сумма весов должна быть положительной")
    return {key: 100.0 * value / total for key, value in weights.items()}


def reweight_component(weights: dict[str, float], component: str, factor: float) -> dict[str, float]:
    """Вес компонента умножается на factor, остальные пропорционально сжимаются до суммы 100."""
    keys = set(COMPONENTS[component])
    inside = sum(weights[k] for k in keys)
    outside = 100.0 - inside
    new_inside = min(inside * factor, 100.0)
    scale_in = new_inside / inside if inside else 0.0
    scale_out = (100.0 - new_inside) / outside if outside else 0.0
    return {k: w * (scale_in if k in keys else scale_out) for k, w in weights.items()}


def drop_metric(weights: dict[str, float], metric: str) -> dict[str, float]:
    """Leave-one-out: вес показателя = 0, остальные растягиваются до 100."""
    return _normalize({k: (0.0 if k == metric else w) for k, w in weights.items()})


def dirichlet_weights(weights: dict[str, float], rng: random.Random, concentration: float) -> dict[str, float]:
    """Случайные веса со средним, равным базовым. Сумма 100, нулевой вес остаётся нулём."""
    draws = {
        k: (rng.gammavariate(w / 100.0 * concentration, 1.0) if w > 0 else 0.0)
        for k, w in weights.items()
    }
    return _normalize(draws)


# ---------- индекс и места при других весах ----------

def missing_price_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {r["resort_id"] for r in rows if not r.get("adult_skipass_avg_rub")}


def scores_with(
    rows: list[dict[str, Any]], weights: dict[str, float], price_override: dict[str, float] | None = None
) -> list[float]:
    """Итоговый индекс при заданных весах. price_override подменяет ценовой балл по resort_id."""
    price_override = price_override or {}
    result = []
    for row in rows:
        total = 0.0
        for key, weight in weights.items():
            value = row[key + "_score"]
            if key == "price" and row["resort_id"] in price_override:
                value = price_override[row["resort_id"]]
            total += value * weight
        result.append(total / 100.0)
    return result


def ranks_with(
    rows: list[dict[str, Any]], weights: dict[str, float], price_override: dict[str, float] | None = None
) -> list[int]:
    return assign_ranks(scores_with(rows, weights, price_override))


def simulate_ranks(
    rows: list[dict[str, Any]], weights: dict[str, float], *, n: int, seed: int,
    concentration: float, price_range: tuple[float, float] = (50.0, 50.0),
) -> list[list[int]]:
    """n симуляций: строка = места всех курортов (в порядке rows) в одной симуляции."""
    rng = random.Random(seed)
    missing = sorted(missing_price_ids(rows))
    low, high = price_range
    sims = []
    for _ in range(n):
        sim_weights = dirichlet_weights(weights, rng, concentration)
        override = {rid: rng.uniform(low, high) for rid in missing}
        sims.append(ranks_with(rows, sim_weights, override))
    return sims


# ---------- интервалы и группы ----------

def percentile(values: list[float], q: float) -> float:
    """Процентиль методом ближайшего ранга: всегда одно из наблюдённых значений."""
    ordered = sorted(values)
    index = max(0, math.ceil(q / 100.0 * len(ordered)) - 1)
    return ordered[min(index, len(ordered) - 1)]


def rank_intervals(
    rows: list[dict[str, Any]], sims: list[list[int]], *, low: float = 5, high: float = 95,
    top_k: tuple[int, ...] = (5, 10, 15),
) -> list[dict[str, Any]]:
    """По строке на курорт: базовое место, интервал мест и вероятность попасть в топ-K."""
    n = len(sims)
    result = []
    for i, row in enumerate(rows):
        column = [sim[i] for sim in sims]
        item = {
            "resort_id": row["resort_id"],
            "name_ru": row["name_ru"],
            "region": row["region"],
            "operating_status": row["operating_status"],
            "popularity_score": round(row["popularity_score_default"], 2),
            "confidence_score": round(row["confidence_score"], 1),
            "rank_default": row["rank_default"],
            "rank_operating": row.get("rank_operating"),
            f"rank_p{low:02.0f}": int(percentile(column, low)),
            "rank_median": int(percentile(column, 50)),
            f"rank_p{high:02.0f}": int(percentile(column, high)),
        }
        item["interval_width"] = item[f"rank_p{high:02.0f}"] - item[f"rank_p{low:02.0f}"]
        for k in top_k:
            item[f"p_top{k}"] = round(sum(r <= k for r in column) / n, 3)
        item["has_price"] = bool(row.get("adult_skipass_avg_rub"))
        item["quality_flags"] = row.get("quality_flags") or ""
        result.append(item)
    return result


def assign_groups(
    intervals: list[dict[str, Any]], rules: list[dict[str, Any]],
    default: dict[str, str], not_operating: dict[str, str],
) -> list[dict[str, Any]]:
    """Добавляет group и group_label. Неработающие курорты всегда в отдельной группе."""
    result = []
    for item in intervals:
        chosen = default
        if item["operating_status"] != "operating":
            chosen = not_operating
        else:
            for rule in rules:
                if item.get(f"p_top{rule['top_k']}", 0.0) >= rule["min_probability"]:
                    chosen = rule
                    break
        result.append({**item, "group": chosen["group"], "group_label": chosen["label"]})
    return result


def mark_distinct_neighbours(intervals: list[dict[str, Any]], low_key: str, high_key: str) -> list[dict[str, Any]]:
    """distinct_from_next = True, если интервал курорта не пересекается с интервалом следующего по месту.

    False означает: порядок этих двух курортов статистически не различим.
    """
    ordered = sorted(intervals, key=lambda r: (r["rank_default"], r["resort_id"]))
    for current, nxt in zip(ordered, ordered[1:] + [None]):
        current["distinct_from_next"] = None if nxt is None else current[high_key] < nxt[low_key]
    return ordered


# ---------- сравнение порядков ----------

def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float:
    """Ранговая корреляция Спирмена (связи получают средний ранг)."""
    ra, rb = _average_ranks(a), _average_ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    var = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return 1.0 if var == 0 else cov / var


def _top(rows: list[dict[str, Any]], ranks: list[int], k: int) -> set[str]:
    return {row["name_ru"] for row, rank in zip(rows, ranks) if rank <= k}


def _compare(rows, base_ranks, new_ranks, k: int = 10) -> dict[str, Any]:
    shifts = [b - n for b, n in zip(base_ranks, new_ranks)]          # >0: поднялся
    worst = max(range(len(shifts)), key=lambda i: (abs(shifts[i]), -base_ranks[i]))
    base_top, new_top = _top(rows, base_ranks, k), _top(rows, new_ranks, k)
    return {
        "spearman": round(spearman(base_ranks, new_ranks), 4),
        "mean_abs_shift": round(sum(abs(s) for s in shifts) / len(shifts), 2),
        "max_abs_shift": abs(shifts[worst]),
        "resort_max_shift": rows[worst]["name_ru"] if shifts[worst] else "",
        f"top{k}_common": len(base_top & new_top),
        f"top{k}_out": "; ".join(sorted(base_top - new_top)),
        f"top{k}_in": "; ".join(sorted(new_top - base_top)),
    }


# ---------- сценарии чувствительности ----------

def component_sensitivity(
    rows: list[dict[str, Any]], weights: dict[str, float], factor: float = 1.2
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """+20% к каждому компоненту. Возвращает (по курортам, сводка по компонентам)."""
    base = ranks_with(rows, weights)
    per_resort = [
        {"resort_id": r["resort_id"], "name_ru": r["name_ru"], "rank_default": b}
        for r, b in zip(rows, base)
    ]
    summary = []
    for component in COMPONENTS:
        new_weights = reweight_component(weights, component, factor)
        new = ranks_with(rows, new_weights)
        for item, b, n in zip(per_resort, base, new):
            item[f"rank_{component}"] = n
            item[f"shift_{component}"] = b - n
        summary.append({
            "component": component,
            "label": COMPONENT_LABELS[component],
            "weight_before": round(sum(weights[k] for k in COMPONENTS[component]), 2),
            "weight_after": round(sum(new_weights[k] for k in COMPONENTS[component]), 2),
            **_compare(rows, base, new),
        })
    return per_resort, summary


def leave_one_out(rows: list[dict[str, Any]], weights: dict[str, float]) -> list[dict[str, Any]]:
    """Сводка по каждому из 13 показателей: насколько меняется порядок без него."""
    base = ranks_with(rows, weights)
    result = []
    for metric, weight in weights.items():
        component = next(c for c, keys in COMPONENTS.items() if metric in keys)
        new = ranks_with(rows, drop_metric(weights, metric))
        result.append({
            "metric": metric, "component": COMPONENT_LABELS[component], "weight": weight,
            **_compare(rows, base, new),
        })
    return sorted(result, key=lambda r: (r["spearman"], -r["mean_abs_shift"]))


def price_bounds(rows: list[dict[str, Any]], weights: dict[str, float], low: float = 0.0, high: float = 100.0) -> list[dict[str, Any]]:
    """Курорты без цены: место при самой высокой цене (балл low), при 50 и при самой низкой (high).

    Остальным курортам без цены оставляем 50, меняем только один курорт за раз.
    swing — сколько мест «стоит» неизвестная цена: чем больше, тем полезнее её собрать.
    """
    index = {r["resort_id"]: i for i, r in enumerate(rows)}
    base = ranks_with(rows, weights)
    result = []
    for rid in sorted(missing_price_ids(rows), key=lambda x: base[index[x]]):
        i = index[rid]
        worst = ranks_with(rows, weights, {rid: low})[i]
        best = ranks_with(rows, weights, {rid: high})[i]
        result.append({
            "resort_id": rid, "name_ru": rows[i]["name_ru"], "rank_default": base[i],
            "rank_if_expensive": worst, "rank_if_cheap": best, "swing": worst - best,
        })
    return sorted(result, key=lambda r: (-r["swing"], r["rank_default"]))


# ---------- запуск ----------

def run_stability(root: Path = PROJECT_ROOT, config: dict[str, Any] | None = None,
                  n_simulations: int | None = None) -> dict[str, Any]:
    """Считает все таблицы шага 6. Файлы не пишет."""
    config = config or load_stability_config(root / "config" / "stability.yaml")
    scenario = config.get("scenario") or load_scenarios(root / "config" / "scenarios.yaml")["primary"]
    weights = load_weights(root / "config" / "weights.yaml")
    check_components(weights)

    rows = build_model(root, scenario)["model_rows"]
    mc = config["monte_carlo"]
    low, high = config["interval"]["low"], config["interval"]["high"]
    sims = simulate_ranks(
        rows, weights, n=n_simulations or mc["n_simulations"], seed=mc["seed"],
        concentration=mc["concentration"], price_range=tuple(mc["missing_price_score"]),
    )
    top_k = tuple(sorted({5, 10, *(rule["top_k"] for rule in config["groups"])}))
    intervals = rank_intervals(rows, sims, low=low, high=high, top_k=top_k)
    intervals = assign_groups(intervals, config["groups"], config["default_group"], config["not_operating_group"])
    intervals = mark_distinct_neighbours(intervals, f"rank_p{low:02.0f}", f"rank_p{high:02.0f}")

    per_resort, component_summary = component_sensitivity(rows, weights, config["component_factor"])
    return {
        "scenario": scenario,
        "weights": weights,
        "config": config,
        "rows": rows,
        "simulations": sims,
        "intervals": intervals,
        "component_by_resort": per_resort,
        "component_summary": component_summary,
        "leave_one_out": leave_one_out(rows, weights),
        "price_bounds": price_bounds(rows, weights, *mc["missing_price_score"]),
    }


def main(root: Path = PROJECT_ROOT) -> None:
    result = run_stability(root)
    mart = root / "data" / "mart"
    write_csv(mart / "rank_intervals.csv", result["intervals"])
    write_csv(mart / "component_sensitivity.csv", result["component_by_resort"])
    write_csv(mart / "component_sensitivity_summary.csv", result["component_summary"])
    write_csv(mart / "leave_one_out.csv", result["leave_one_out"])
    write_csv(mart / "price_bounds.csv", result["price_bounds"])
    groups: dict[str, list[str]] = {}
    for item in result["intervals"]:
        groups.setdefault(f"{item['group']} {item['group_label']}", []).append(item["name_ru"])
    write_json(mart / "stability_summary.json", {
        "scenario": result["scenario"],
        "n_simulations": len(result["simulations"]),
        "config": result["config"],
        "groups": groups,
    })

    print(f"Сценарий: {result['scenario']}, симуляций: {len(result['simulations'])}")
    for name, members in sorted(groups.items()):
        print(f"  {name}: {', '.join(members)}")
    print("\nСильнее всего порядок зависит от (leave-one-out, по Спирмену):")
    for item in result["leave_one_out"][:3]:
        print(f"  {item['metric']:<14} rho={item['spearman']:.3f}, сдвиг в среднем {item['mean_abs_shift']} места")


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    main()
