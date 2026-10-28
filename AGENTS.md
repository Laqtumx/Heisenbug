# AGENTS.md

Repository notes for future sessions. Read this before changing the pipeline or
the rules.

## What this project is

CleanSlate, an ERP data migration platform. Legacy files go in, validated and
cleaned records go out. The pipeline runs in stages: ingest, profile, validate,
clean, map, dry-run, load, verify, sign-off, rollback.

## Layout

- `backend/app/pipeline/` — demo data generator, file readers, ingest, profile,
  detection harness.
- `backend/app/rules/` — the rule engine. `config/rules.json` holds the rules;
  `checks.py` implements the check types.
- `backend/db/migrations/` — SQL migrations, applied by `app/db/migrate.py`.
- `backend/tests/` — pytest suite, runs against real Postgres and Mongo.
- `scripts/` — demo data generation and database startup.
- `data/legacy/` — generated demo CSVs. Not tracked; regenerate with
  `make demo-data`.

## Commands

```
make setup       # venv, deps, databases, migrations
make demo-data   # regenerate data/legacy (fixed seed, byte-identical)
make test        # full pytest suite
make api         # FastAPI on :8000
```

Tests need the Postgres and Mongo containers. `make db-up` starts them on 5433
and 27018.

## Rules for changing the rule engine

Rules are configuration, not code. Add or change a rule in
`rules/config/rules.json`; only add a new check type to `checks.py` when the
logic is genuinely new. `loader.py` resolves the `{entity}` and threshold
placeholders.

Two invariants hold the design together.

Every rule must be deterministic and reproducible. Choose survivors, canonical
records and reported records by a business key or a value, never by file
position. The source files are shuffled, so a position-based choice reports a
different record on every run and the finding cannot be reconciled.

Dates must be measured against `settings.reference_date`, not `date.today()`.
Staleness, future dating and ageing all read this one value. Using today makes
a batch validated in January disagree with the same batch revalidated in March.

## Rules for changing the generator

The generator injects labelled defects and writes `defects_manifest.csv`. The
detection harness scores the rule engine against that manifest, so the two
sides must stay honest with each other.

- One defect per record. Injectors draw disjoint rows via `_sample_clean`; the
  used-row set is reset per entity because row positions are per frame.
- Never blank a master business key. A blank supplier code orphans every
  purchase order that references it, so one labelled defect becomes hundreds of
  unlabelled findings.
- Every injected defect must be detectable. A value the rule strips before
  matching (a whitespace-only UoM alias) or tolerates (an all-upper name) is not
  a defect. Where the rule and the generator share a vocabulary, read it from
  the rule configuration so the two cannot drift apart.
- Clean data must pass every rule. Unbalanced ledgers, colliding names and
  future due dates were all generator artefacts that made the rule engine look
  wrong. Fix the generator before weakening a rule.

Run the detection test after any change to either side:

```
cd backend && .venv/bin/python -m pytest tests/test_detection_rate.py -q
```

Current result on the full 50k dataset: 100% recall, 99.8% precision.

## Testing notes

Tests run against the real databases, not mocks. Every fixture that inserts
rows must clean them up, before and after, or a second run matches the first
run's data and fails for the wrong reason. `tests/test_rls_and_rbac.py` and
`tests/test_pipeline_integration.py` both purge by a `source_ref` or email
prefix.
