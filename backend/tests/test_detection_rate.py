"""The demo must keep catching essentially every injected defect.

This is the project's headline claim: the generator plants known, labelled
defects and the rule engine finds them. The test regenerates a scaled-down
dataset with the same defect plan as the full one, runs every rule against it in
memory, and measures recall against the manifest.

The threshold is 0.98 rather than 1.0 to leave a little room for the fuzzy
matcher, which depends on the generated name pool. Anything below it means a
rule or an injector has drifted apart from the other, which is the failure this
test exists to catch.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.pipeline.demo_data import DemoDataGenerator, RecordCounts
from app.pipeline.detection import DetectionReport, measure
from app.pipeline.entities import ENTITIES_BY_NAME
from app.rules.checks import RULES, RuleContext
from app.rules.loader import expand_for_entity, load_ruleset

pytestmark = pytest.mark.detection

RECALL_TARGET = 0.98
PRECISION_TARGET = 0.95


def _small_counts() -> RecordCounts:
    """About a fifth of the full dataset, so the test stays quick."""
    full = RecordCounts()
    return RecordCounts(
        **{name: max(20, value // 5) for name, value in full.__dict__.items()}
    )


def _collect_findings(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Run every applicable rule and return the findings as a frame.

    This mirrors the rule loop in `run_validation` without the database, so the
    test exercises the real rule implementations and the real loader.
    """
    ruleset = load_ruleset()
    ctx = RuleContext(frames=frames, specs=ENTITIES_BY_NAME)
    rows: list[dict[str, object]] = []

    for entity, frame in frames.items():
        spec = ENTITIES_BY_NAME.get(entity)
        if spec is None:
            continue
        for base_rule in ruleset.for_entity(entity):
            implementation = RULES[base_rule.rule_type]
            rule = expand_for_entity(base_rule, entity)
            if not rule.params:
                continue
            for finding in implementation(frame, spec, rule.params, ctx):
                rows.append(
                    {
                        "entity": entity,
                        "record_id": finding.get("record_id"),
                        "rule_id": base_rule.rule_id,
                        "field": finding.get("field"),
                    }
                )
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def report() -> DetectionReport:
    dataset = DemoDataGenerator(seed=20260101, counts=_small_counts()).generate()
    findings = _collect_findings(dataset.frames)
    return measure(dataset.manifest_frame(), findings)


def test_recall_meets_target(report: DetectionReport) -> None:
    assert report.recall >= RECALL_TARGET, (
        f"recall {report.recall:.3f} is below {RECALL_TARGET}.\n"
        + "\n".join(report.summary_lines())
    )


def test_precision_meets_target(report: DetectionReport) -> None:
    assert report.precision >= PRECISION_TARGET, (
        f"precision {report.precision:.3f} is below {PRECISION_TARGET}.\n"
        + "\n".join(report.summary_lines())
    )


def test_every_defect_type_is_detected(report: DetectionReport) -> None:
    """No injected defect type is missed entirely."""
    undetected = [name for name, result in report.by_type.items() if result.detected == 0]
    assert not undetected, f"defect types with no detections: {undetected}"


def test_manifest_covers_every_entity_that_has_defects(report: DetectionReport) -> None:
    """The manifest is the ground truth, so it must not be empty."""
    assert report.total_injected > 0
    assert report.total_detected > 0
