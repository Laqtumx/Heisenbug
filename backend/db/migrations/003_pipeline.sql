-- 003: migration pipeline tables.
--
-- Everything the pipeline produces hangs off a batch_id. Records that land in
-- the target ERP tables carry (batch_id, source_ref) so a rollback is a single
-- delete keyed on the batch.

create table if not exists public.migration_batches (
  id              uuid primary key default gen_random_uuid(),
  batch_code      text not null unique,
  name            text not null,
  source_system   text not null default 'legacy_erp',
  target_system   text not null default 'cleanslate_erp',
  status          batch_status not null default 'created',
  fiscal_year_from integer,
  fiscal_year_to   integer,
  created_by      uuid references public.app_users(id),
  notes           text not null default '',
  health_score_before numeric(5,2),
  health_score_after  numeric(5,2),
  record_count_in     integer not null default 0,
  record_count_loaded integer not null default 0,
  record_count_archived integer not null default 0,
  record_count_rejected integer not null default 0,
  started_at      timestamptz,
  completed_at    timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index if not exists migration_batches_status_idx on public.migration_batches (status);

drop trigger if exists migration_batches_touch on public.migration_batches;
create trigger migration_batches_touch before update on public.migration_batches
  for each row execute function public.touch_updated_at();

-- One row per stage per batch. Re-running a stage upserts and bumps attempts.
create table if not exists public.migration_stages (
  id            uuid primary key default gen_random_uuid(),
  batch_id      uuid not null references public.migration_batches(id) on delete cascade,
  stage         stage_name not null,
  status        stage_status not null default 'pending',
  attempt       integer not null default 0,
  progress      numeric(5,2) not null default 0,
  summary       jsonb not null default '{}'::jsonb,
  error_message text,
  started_at    timestamptz,
  finished_at   timestamptz,
  duration_ms   integer,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (batch_id, stage)
);

create index if not exists migration_stages_batch_idx on public.migration_stages (batch_id);

drop trigger if exists migration_stages_touch on public.migration_stages;
create trigger migration_stages_touch before update on public.migration_stages
  for each row execute function public.touch_updated_at();

-- Raw file uploads. The bytes live in Mongo; this table is the catalogue.
create table if not exists public.ingested_files (
  id            uuid primary key default gen_random_uuid(),
  batch_id      uuid not null references public.migration_batches(id) on delete cascade,
  entity        text not null,
  file_name     text not null,
  file_type     text not null,
  file_size     bigint not null default 0,
  checksum_sha256 text not null,
  mongo_collection text not null default 'raw_records',
  mongo_document_id text,
  row_count     integer not null default 0,
  uploaded_by   uuid references public.app_users(id),
  created_at    timestamptz not null default now(),
  unique (batch_id, entity, checksum_sha256)
);

create index if not exists ingested_files_batch_idx on public.ingested_files (batch_id, entity);

-- Column-level profile output. One row per entity/column.
create table if not exists public.column_profiles (
  id              uuid primary key default gen_random_uuid(),
  batch_id        uuid not null references public.migration_batches(id) on delete cascade,
  entity          text not null,
  column_name     text not null,
  row_count       bigint not null default 0,
  null_count      bigint not null default 0,
  null_pct        numeric(6,3) not null default 0,
  distinct_count  bigint not null default 0,
  inferred_type   text not null default 'unknown',
  format_patterns jsonb not null default '[]'::jsonb,
  min_value       text,
  max_value       text,
  mean_value      numeric(20,4),
  stddev_value    numeric(20,4),
  outlier_count   bigint not null default 0,
  sample_values   jsonb not null default '[]'::jsonb,
  created_at      timestamptz not null default now(),
  unique (batch_id, entity, column_name)
);

create table if not exists public.validation_findings (
  id              uuid primary key default gen_random_uuid(),
  batch_id        uuid not null references public.migration_batches(id) on delete cascade,
  rule_id         text not null,
  rule_category   text not null,
  severity        severity not null,
  entity          text not null,
  record_id       text,
  field           text,
  message         text not null,
  details         jsonb not null default '{}'::jsonb,
  suggested_fix   text,
  auto_fixable    boolean not null default false,
  status          finding_status not null default 'open',
  detected_at     timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index if not exists validation_findings_batch_idx on public.validation_findings (batch_id);
create index if not exists validation_findings_sev_idx on public.validation_findings (batch_id, severity);
create index if not exists validation_findings_entity_idx on public.validation_findings (batch_id, entity);
create index if not exists validation_findings_status_idx on public.validation_findings (status);
create index if not exists validation_findings_rule_idx on public.validation_findings (rule_id);

drop trigger if exists validation_findings_touch on public.validation_findings;
create trigger validation_findings_touch before update on public.validation_findings
  for each row execute function public.touch_updated_at();

-- Human-in-the-loop queue. Holds the legacy record and the proposed record so
-- the UI can show a side-by-side diff without re-reading Mongo.
create table if not exists public.review_queue (
  id                uuid primary key default gen_random_uuid(),
  batch_id          uuid not null references public.migration_batches(id) on delete cascade,
  finding_id        uuid references public.validation_findings(id) on delete cascade,
  entity            text not null,
  record_id         text,
  reason            text not null,
  severity          severity not null default 'warn',
  legacy_record     jsonb not null default '{}'::jsonb,
  proposed_record   jsonb not null default '{}'::jsonb,
  ai_explanation    text,
  ai_suggestion     jsonb,
  status            review_status not null default 'pending',
  decided_by        uuid references public.app_users(id),
  decided_at        timestamptz,
  decision_note     text not null default '',
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create index if not exists review_queue_batch_idx on public.review_queue (batch_id, status);
create index if not exists review_queue_entity_idx on public.review_queue (batch_id, entity);

drop trigger if exists review_queue_touch on public.review_queue;
create trigger review_queue_touch before update on public.review_queue
  for each row execute function public.touch_updated_at();

create table if not exists public.field_mappings (
  id                uuid primary key default gen_random_uuid(),
  batch_id          uuid not null references public.migration_batches(id) on delete cascade,
  entity            text not null,
  legacy_column     text not null,
  target_column     text not null,
  confidence        numeric(5,4) not null default 0,
  method            mapping_method not null default 'heuristic',
  rationale         text not null default '',
  transform         text not null default 'identity',
  approved          boolean not null default false,
  approved_by       uuid references public.app_users(id),
  approved_at       timestamptz,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  unique (batch_id, entity, legacy_column)
);

create index if not exists field_mappings_batch_idx on public.field_mappings (batch_id, entity);

drop trigger if exists field_mappings_touch on public.field_mappings;
create trigger field_mappings_touch before update on public.field_mappings
  for each row execute function public.touch_updated_at();

-- Immutable audit trail. A trigger rejects UPDATE and DELETE outright, and a
-- statement-level trigger blocks TRUNCATE, which row triggers do not see.
create table if not exists public.audit_log (
  id            bigserial primary key,
  occurred_at   timestamptz not null default now(),
  actor_id      uuid,
  actor_email   text not null default 'system',
  actor_role    text not null default 'system',
  action        text not null,
  entity        text not null default '',
  entity_id     text,
  batch_id      uuid,
  stage         text,
  before_state  jsonb,
  after_state   jsonb,
  metadata      jsonb not null default '{}'::jsonb,
  request_id    text,
  ip_address    text
);

create index if not exists audit_log_batch_idx on public.audit_log (batch_id, occurred_at desc);
create index if not exists audit_log_actor_idx on public.audit_log (actor_id, occurred_at desc);
create index if not exists audit_log_action_idx on public.audit_log (action);

create or replace function public.audit_log_immutable() returns trigger
language plpgsql as $$
begin
  raise exception 'audit_log is append-only: % is not permitted', tg_op
    using errcode = 'check_violation';
end $$;

drop trigger if exists audit_log_no_update on public.audit_log;
create trigger audit_log_no_update before update or delete on public.audit_log
  for each row execute function public.audit_log_immutable();

-- Row triggers never fire on TRUNCATE, so it needs its own guard.
drop trigger if exists audit_log_no_truncate on public.audit_log;
create trigger audit_log_no_truncate before truncate on public.audit_log
  for each statement execute function public.audit_log_immutable();

-- Reconciliation snapshots produced by dry run and by post-load verification.
create table if not exists public.reconciliation_reports (
  id                    uuid primary key default gen_random_uuid(),
  batch_id              uuid not null references public.migration_batches(id) on delete cascade,
  phase                 text not null,
  health_score          numeric(5,2),
  counts                jsonb not null default '{}'::jsonb,
  financial_totals      jsonb not null default '{}'::jsonb,
  checksums             jsonb not null default '{}'::jsonb,
  per_entity            jsonb not null default '{}'::jsonb,
  balanced              boolean not null default false,
  created_at            timestamptz not null default now()
);

create index if not exists reconciliation_reports_batch_idx on public.reconciliation_reports (batch_id, phase);
