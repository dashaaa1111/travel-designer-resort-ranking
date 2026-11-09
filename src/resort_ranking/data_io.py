"""Чтение входных файлов и запись результатов. Расчётов здесь нет."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import yaml


def load_matrix(path: Path) -> list[dict[str, Any]]:
    """JSON-матрица с заголовком в первой строке -> список словарей."""
    matrix = json.loads(path.read_text(encoding="utf-8"))
    header = matrix[0]
    return [dict(zip(header, row, strict=True)) for row in matrix[1:]]


def parse_rosstat_csv(path: Path) -> list[dict[str, Any]]:
    """CSV Росстата с разделителем ';'. Колонка 'Ряд' -> ключ 'series'."""
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter=";")
        rows: list[dict[str, Any]] = []
        for row in reader:
            clean: dict[str, Any] = {"series": row["Ряд"]}
            for key, value in row.items():
                if key == "Ряд":
                    continue
                clean[key] = float(value) if value not in (None, "") else None
            rows.append(clean)
        return rows


def load_weights(path: Path) -> dict[str, float]:
    """Читает веса из YAML и проверяет, что сумма равна 100."""
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    weights = {key: float(value) for key, value in config["weights"].items()}
    total = sum(weights.values())
    if abs(total - 100.0) > 0.001:
        raise ValueError(f"Сумма весов должна быть 100, сейчас {total}")
    return weights


def load_scenarios(path: Path) -> dict[str, Any]:
    """Читает config/scenarios.yaml и проверяет, что основной сценарий существует."""
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if config["primary"] not in config["scenarios"]:
        raise ValueError(f"primary={config['primary']!r} нет среди сценариев")
    return config


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
