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

# Canonical columns that may map to null (source view has no such column).
NULLABLE_COLUMNS = {"item_description", "warehouse", "balance_date", "reference"}

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_ .#$-]+$")

# A column mapping value: physical column name, list of columns forming a
# composite key (concatenated with '|'), {product: [cols]} for a derived
# multiplication (e.g. WIP value x completion ratio), or None when the view
# lacks it.
ColumnMap = str | list[str] | dict | None


@dataclass
class DatasetConfig:
    name: str
    kind: str  # "movement" | "balance"
    table: str
    columns: dict[str, ColumnMap]

    @property
    def date_column(self) -> str:
        return "movement_date" if self.kind == "movement" else "balance_date"

    @property
    def has_date(self) -> bool:
        """False for current-state views with no snapshot/movement date column."""
        return self.columns.get(self.date_column) is not None


@dataclass
class Config:
    datasets: dict[str, DatasetConfig]
    movement_types: dict[str, list[str]]
    reconcile_exclusions: list[dict] = field(default_factory=list)
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
        for canon, src in columns.items():
            if src is None:
                if canon not in NULLABLE_COLUMNS:
                    raise ValueError(f"Dataset {name}: column {canon!r} cannot be null")
            elif isinstance(src, list):
                if not src:
                    raise ValueError(f"Dataset {name}: column {canon!r} maps to an empty list")
                for part in src:
                    _validate_identifier(part, f"dataset {name} columns")
            elif isinstance(src, dict):
                factors = src.get("product")
                if set(src) != {"product"} or not isinstance(factors, list) or len(factors) < 2:
                    raise ValueError(
                        f"Dataset {name}: column {canon!r} derived mapping must be "
                        f"{{product: [col|number, ...]}} with at least two factors"
                    )
                if not any(isinstance(p, str) for p in factors):
                    raise ValueError(f"Dataset {name}: column {canon!r} product needs at least one column")
                for part in factors:
                    if isinstance(part, str):
                        _validate_identifier(part, f"dataset {name} columns")
                    elif not isinstance(part, (int, float)):
                        raise ValueError(
                            f"Dataset {name}: column {canon!r} product factors must be "
                            f"column names or numbers, got {part!r}"
                        )
            else:
                _validate_identifier(src, f"dataset {name} columns")
        datasets[name] = DatasetConfig(name=name, kind=kind, table=spec["table"], columns=columns)

    exclusions = raw.get("reconcile_exclusions") or []
    allowed_keys = {"movement_type", "warehouse", "reference_prefix"}
    for i, rule in enumerate(exclusions):
        if not isinstance(rule, dict) or not rule:
            raise ValueError(f"reconcile_exclusions[{i}] must be a non-empty mapping")
        unknown = rule.keys() - allowed_keys
        if unknown:
            raise ValueError(
                f"reconcile_exclusions[{i}]: unknown key(s) {sorted(unknown)}; "
                f"allowed: {sorted(allowed_keys)}"
            )

    analysis = raw.get("analysis", {})
    return Config(
        datasets=datasets,
        movement_types={k: list(v) for k, v in raw.get("movement_types", {}).items()},
        reconcile_exclusions=exclusions,
        issues_stored_positive=bool(raw.get("issues_stored_positive", True)),
        dormant_days=int(analysis.get("dormant_days", 90)),
        outlier_zscore=float(analysis.get("outlier_zscore", 3.0)),
        variance_tolerance=float(analysis.get("variance_tolerance", 0.5)),
        path=path,
    )
