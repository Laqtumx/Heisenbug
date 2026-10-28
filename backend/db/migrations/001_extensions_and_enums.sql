-- 001: extensions, enum types and the Postgres roles the RLS model relies on.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------- app roles --
-- Supabase defines `anon`, `authenticated` and `service_role`. They are created
-- here only when missing so this migration is safe on Supabase too.
do $roles$
declare
  r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if not exists (select 1 from pg_roles where rolname = r) then
      execute format('create role %I nologin noinherit', r);
    end if;
  end loop;
end
$roles$;

-- service_role is the trusted backend path and bypasses RLS, mirroring Supabase.
alter role service_role bypassrls;

-- --------------------------------------------------------------------- enums --
do $enums$
begin
  if not exists (select 1 from pg_type where typname = 'app_role') then
    create type app_role as enum (
      'admin', 'migration_engineer', 'finance_controller', 'inventory_manager', 'supplier'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'batch_status') then
    create type batch_status as enum (
      'created', 'ingesting', 'ingested', 'profiled', 'validating', 'validated',
      'cleaning', 'cleaned', 'mapping', 'mapped', 'dry_running', 'dry_run_complete',
      'loading', 'loaded', 'verifying', 'verified', 'signed_off', 'rolled_back', 'failed'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'stage_name') then
    create type stage_name as enum (
      'ingest', 'profile', 'validate', 'clean', 'map', 'dry_run', 'load', 'verify', 'signoff', 'rollback'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'stage_status') then
    create type stage_status as enum ('pending', 'running', 'succeeded', 'failed', 'skipped');
  end if;

  if not exists (select 1 from pg_type where typname = 'severity') then
    create type severity as enum ('blocker', 'warn', 'info');
  end if;

  if not exists (select 1 from pg_type where typname = 'finding_status') then
    create type finding_status as enum (
      'open', 'auto_fixed', 'pending_review', 'approved', 'rejected', 'ignored', 'applied'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'review_status') then
    create type review_status as enum ('pending', 'approved', 'rejected', 'bulk_approved');
  end if;

  if not exists (select 1 from pg_type where typname = 'mapping_method') then
    create type mapping_method as enum ('heuristic', 'ai', 'human');
  end if;
end
$enums$;

-- ------------------------------------------------------------------ helpers --
create or replace function public.touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

-- Reads the application role out of the Supabase JWT. The claim lives under
-- app_metadata so it cannot be set by a self-service signup.
create or replace function public.app_role() returns text
language sql stable as $$
  select coalesce(
    nullif(current_setting('request.jwt.claims', true), '')::jsonb
      -> 'app_metadata' ->> 'role',
    'anon'
  )
$$;

create or replace function public.app_supplier_id() returns uuid
language sql stable as $$
  select nullif(
    coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
      -> 'app_metadata' ->> 'supplier_id',
    ''
  )::uuid
$$;

create or replace function public.current_app_user() returns uuid
language sql stable as $$
  select auth.uid()
$$;

create or replace function public.has_role(variadic roles text[]) returns boolean
language sql stable as $$
  select public.app_role() = any(roles)
$$;
