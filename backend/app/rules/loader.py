"""Rule configuration loading and resolution.

Rules live in JSON so a new check is a config change, not a code change. This
module loads the file, expands the ``$placeholder`` references against the
threshold block and the entity metadata, and hands the engine a flat list of
concrete checks to run.

Placeholders keep a threshold in exactly one place: ``$stale_activity_years``
appears in the JSON and in ``.env``, never hardcoded in a rule implementation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import settings
from app.pipeline.entities import ENTITIES, ENTITIES_BY_NAME, EntitySpec

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "rules.json"

ALL_ENTITIES = "$all"


class RuleConfigError(ValueError):
    """Raised when the rule configuration is malformed."""


@dataclass(frozen=True)
class Rule:
    rule_id: str
    name: str
    category: str
    rule_type: str
    severity: str
    auto_fixable: bool
    entities: tuple[str, ...]
    params: dict[str, Any]
    message: str
    suggested_fix: str
    enabled: bool = True

    def applies_to(self, entity: str) -> bool:
        return ALL_ENTITIES in self.entities or entity in self.entities


@dataclass
class RuleSet:
    version: str
    rules: list[Rule]
    thresholds: dict[str, Any] = field(default_factory=dict)

    def for_entity(self, entity: str) -> list[Rule]:
        return [r for r in self.rules if r.enabled and r.applies_to(entity)]

    def by_id(self, rule_id: str) -> Rule | None:
        return next((r for r in self.rules if r.rule_id == rule_id), None)

    @property
    def categories(self) -> list[str]:
        return sorted({r.category for r in self.rules})


def _threshold_placeholders() -> dict[str, Any]:
    """Threshold values a rule may reference with a $name placeholder."""
    return {
        "stale_activity_years": settings.archive_after_years,
        "fuzzy_duplicate_score": settings.fuzzy_duplicate_threshold,
        "salary_outlier_multiplier": 5.0,
        "zscore_outlier_threshold": 4.0,
        "iqr_outlier_multiplier": 3.0,
        "earliest_plausible_date": "1990-01-01",
    }


def _entity_placeholders(entity: str) -> dict[str, Any]:
    spec: EntitySpec = ENTITIES_BY_NAME[entity]
    return {
        "primary_key": spec.primary_key,
        "mandatory_columns": spec.mandatory_columns,
        "all_columns": spec.column_names,
        "numeric_columns": [c.name for c in spec.columns if c.dtype in ("int", "float")],
        "date_columns": [c.name for c in spec.columns if c.dtype == "date"],
        "sensitive_columns": spec.sensitive_columns,
        "target_table": spec.target_table,
    }


def _resolve(value: Any, placeholders: dict[str, Any]) -> Any:
    """Replace $name references, recursing through lists and dicts."""
    if isinstance(value, str):
        if value.startswith("$"):
            key = value[1:]
            if key not in placeholders:
                raise RuleConfigError(f"Unknown placeholder ${key}")
            return placeholders[key]
        return value
    if isinstance(value, list):
        return [_resolve(v, placeholders) for v in value]
    if isinstance(value, dict):
        return {k: _resolve(v, placeholders) for k, v in value.items()}
    return value


def _resolve_params(params: dict[str, Any], entity: str) -> dict[str, Any]:
    """Resolve placeholders, then drop any parameter left empty.

    A rule that names a column an entity does not have (for example the date
    rule running against the UoM master) ends up with an empty column list and
    simply does nothing, rather than raising.
    """
    merged = {**_threshold_placeholders(), **_entity_placeholders(entity)}
    resolved = _resolve(params, merged)
    return {
        k: v
        for k, v in resolved.items()
        if v not in (None, [], {}, "")
    }


@lru_cache
def load_ruleset(path: str | None = None) -> RuleSet:
    config_path = Path(path) if path else CONFIG_PATH
    if not config_path.exists():
        raise RuleConfigError(f"Rule configuration not found at {config_path}")

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    entries = raw.get("rules")
    if not isinstance(entries, list) or not entries:
        raise RuleConfigError("Rule configuration must contain a non-empty 'rules' list")

    seen: set[str] = set()
    rules: list[Rule] = []
    for entry in entries:
        rule_id = entry.get("rule_id")
        if not rule_id:
            raise RuleConfigError("Every rule needs a rule_id")
        if rule_id in seen:
            raise RuleConfigError(f"Duplicate rule_id {rule_id!r}")
        seen.add(rule_id)

        rule_type = entry.get("rule_type")
        if not rule_type:
            raise RuleConfigError(f"Rule {rule_id} has no rule_type")

        entities = tuple(entry.get("entities") or (ALL_ENTITIES,))
        rules.append(
            Rule(
                rule_id=rule_id,
                name=entry.get("name", rule_id),
                category=entry.get("category", "uncategorised"),
                rule_type=rule_type,
                severity=entry.get("severity", "warn"),
                auto_fixable=bool(entry.get("auto_fixable", False)),
                entities=entities,
                params=entry.get("params", {}) or {},
                message=entry.get("message", ""),
                suggested_fix=entry.get("suggested_fix", ""),
                enabled=bool(entry.get("enabled", True)),
            )
        )

    logger.info("loaded %s validation rules (version %s)", len(rules), raw.get("version"))
    return RuleSet(version=raw.get("version", "0"), rules=rules, thresholds=raw.get("thresholds", {}))


def expand_for_entity(rule: Rule, entity: str) -> Rule:
    """Return a copy of the rule with placeholders resolved for one entity."""
    return Rule(
        rule_id=rule.rule_id,
        name=rule.name,
        category=rule.category,
        rule_type=rule.rule_type,
        severity=rule.severity,
        auto_fixable=rule.auto_fixable,
        entities=rule.entities,
        params=_resolve_params(rule.params, entity),
        message=rule.message,
        suggested_fix=rule.suggested_fix,
        enabled=rule.enabled,
    )


def describe_rules() -> list[dict[str, Any]]:
    ruleset = load_ruleset()
    return [
        {
            "rule_id": r.rule_id,
            "name": r.name,
            "category": r.category,
            "rule_type": r.rule_type,
            "severity": r.severity,
            "auto_fixable": r.auto_fixable,
            "entities": list(r.entities),
            "enabled": r.enabled,
        }
        for r in ruleset.rules
    ]


def validate_rule_types(known_types: set[str]) -> list[str]:
    """Return rule types in config that have no registered implementation."""
    ruleset = load_ruleset()
    return sorted({r.rule_type for r in ruleset.rules if r.rule_type not in known_types})
