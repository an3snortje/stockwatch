"""Load and validate the dataset-mapping configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

MOVEMENT_COLUMNS = {
    "item_code",
    "item_description",
    "warehouse",
    "movement_date",
    "movement_type",
    "quantity",
    "reference",
}
BALANCE_COLUMNS = {"item_code", "item_description", "warehouse", "balance_date", "quantity"}

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_ .#$-]+$")


@dataclass
class DatasetConfig:
    name: str
    kind: str  # "movement" | "balance"
    table: str
    columns: dict[str, str]

    @property
    def date_column(self) -> str:
        return "movement_date" if self.kind == "movement" else "balance_date"


@dataclass
class Config:
    datasets: dict[str, DatasetConfig]
    movement_types: dict[str, list[str]]
    issues_stored_positive: bool = True
    dormant_days: int = 90
    outlier_zscore: float = 3.0
    variance_tolerance: float = 0.5
    path: Path | None = field(default=None, repr=False)


def _validate_identifier(value: str, context: str) -> str:
    if not _IDENTIFIER_RE.match(value):
        raise ValueError(f"Unsafe SQL identifier {value!r} in {context}")
    return value


def load_config(path: str | Path) -> Config:
    path = Path(path)
    raw = yaml.safe_load(path.read_text())

    datasets: dict[str, DatasetConfig] = {}
    for name, spec in raw["datasets"].items():
        kind = spec["kind"]
        if kind not in ("movement", "balance"):
            raise ValueError(f"Dataset {name}: kind must be 'movement' or 'balance', got {kind!r}")
        required = MOVEMENT_COLUMNS if kind == "movement" else BALANCE_COLUMNS
        columns = spec["columns"]
        missing = required - columns.keys()
        if missing:
            raise ValueError(f"Dataset {name}: missing column mappings {sorted(missing)}")
        for part in spec["table"].split("."):
            _validate_identifier(part, f"dataset {name} table")
        for src in columns.values():
            _validate_identifier(src, f"dataset {name} columns")
        datasets[name] = DatasetConfig(name=name, kind=kind, table=spec["table"], columns=columns)

    analysis = raw.get("analysis", {})
    return Config(
        datasets=datasets,
        movement_types={k: list(v) for k, v in raw.get("movement_types", {}).items()},
        issues_stored_positive=bool(raw.get("issues_stored_positive", True)),
        dormant_days=int(analysis.get("dormant_days", 90)),
        outlier_zscore=float(analysis.get("outlier_zscore", 3.0)),
        variance_tolerance=float(analysis.get("variance_tolerance", 0.5)),
        path=path,
    )
