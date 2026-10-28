#!/usr/bin/env python
"""Generate the synthetic legacy ERP dataset used by the demo and the tests.

Examples:
    python scripts/generate_demo_data.py                     # default ~50k rows
    python scripts/generate_demo_data.py --out data/legacy   # custom location
    python scripts/generate_demo_data.py --seed 7 --small    # fast, for CI
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.pipeline.demo_data import (  # noqa: E402
    DEFAULT_SEED,
    DefectPlan,
    DemoDataGenerator,
    RecordCounts,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "data" / "legacy"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a synthetic legacy ERP dataset with labelled defects."
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="RNG seed")
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="multiply every record count by this factor",
    )
    parser.add_argument("--small", action="store_true", help="~10%% of the rows, for CI")
    parser.add_argument(
        "--no-manifest", action="store_true", help="skip writing defects_manifest.csv"
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    scale = args.scale
    if args.small:
        scale *= 0.1

    counts = RecordCounts()
    if scale != 1.0:
        for field_name in counts.__dataclass_fields__:
            setattr(counts, field_name, max(1, int(getattr(counts, field_name) * scale)))

    plan = DefectPlan()
    if scale != 1.0:
        for field_name in plan.__dataclass_fields__:
            setattr(plan, field_name, max(0, int(getattr(plan, field_name) * scale)))

    generator = DemoDataGenerator(seed=args.seed, counts=counts, plan=plan)
    dataset = generator.generate()

    out_dir: Path = args.out
    written = dataset.write(out_dir)

    if args.no_manifest:
        manifest = written.pop("defects_manifest", None)
        if manifest and manifest.exists():
            manifest.unlink()

    if not args.quiet:
        print()
        print(f"dataset written to {out_dir}")
        print(f"  entities       {len(dataset.frames)}")
        print(f"  total records  {dataset.total_records:,}")
        print(f"  labelled defects {len(dataset.defects):,}")
        print()
        print("files:")
        for name, path in sorted(written.items()):
            rows = len(dataset.frames.get(name, []))
            print(f"  {path.name:34s} {rows:>8,} rows")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
