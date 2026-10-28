-- Local development: a NON-superuser login role for the RLS-enforced path.
--
-- Postgres superusers always bypass Row Level Security, so connecting the
-- "authenticated" path as the database owner would silently disable every
-- policy and make the RBAC demo meaningless. This role exists purely so the
-- policies are genuinely exercised locally, exactly as they are on Supabase.
--
-- On Supabase this file never runs (the auth schema already exists) and the
-- backend connects as the platform `authenticated` role instead.

do $approle$
begin
  if not exists (select 1 from pg_roles where rolname = 'cleanslate_authenticated') then
    create role cleanslate_authenticated
      login
      password 'cleanslate_authenticated'
      nosuperuser
      nocreatedb
      nocreaterole
      noinherit
      nobypassrls;
  end if;
end
$approle$;

grant authenticated to cleanslate_authenticated;
grant anon to cleanslate_authenticated;

-- Read the RBAC tables directly: they are pure reference data.
grant usage on schema public to cleanslate_authenticated;

-- Table grants mirror what Supabase hands the `authenticated` role.
do $grants$
declare
  t text;
begin
  foreach t in array array[
    'roles', 'permissions', 'role_permissions', 'app_users', 'migration_batches',
    'migration_stages', 'ingested_files', 'column_profiles', 'validation_findings',
    'review_queue', 'field_mappings', 'audit_log', 'reconciliation_reports',
    'categories', 'uom', 'tax_codes', 'suppliers', 'customers', 'items',
    'supplier_catalog', 'chart_of_accounts', 'gl_balances', 'purchase_orders',
    'invoices', 'inventory_stock', 'employees_payroll_summary'
  ] loop
    execute format('grant select on public.%I to authenticated', t);
  end loop;

  foreach t in array array['review_queue', 'validation_findings', 'field_mappings', 'app_users', 'items'] loop
    execute format('grant update on public.%I to authenticated', t);
  end loop;

  foreach t in array array[
    'migration_batches', 'migration_stages', 'ingested_files', 'column_profiles',
    'validation_findings', 'review_queue', 'field_mappings', 'reconciliation_reports'
  ] loop
    execute format('grant insert, update, delete on public.%I to authenticated', t);
  end loop;
end
$grants$;

-- Sequences: audit_log.id is bigserial and app code reads it back.
grant usage, select on all sequences in schema public to authenticated;
