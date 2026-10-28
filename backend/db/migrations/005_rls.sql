-- 005: Row Level Security.
--
-- Model: the API connects in two ways.
--   1. service_role  — the trusted pipeline path. BYPASSRLS, used for writes.
--   2. authenticated — a per-request session that sets request.jwt.claims from
--                      the verified JWT, so Postgres itself enforces the matrix.
--
-- Nothing in these policies reads a client-supplied value. The role and the
-- supplier identity come out of the JWT, which only the auth service can sign.

-- --------------------------------------------------------------- helpers ---
create or replace function public.app_supplier_code() returns text
language sql stable as $$
  select nullif(
    coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
      -> 'app_metadata' ->> 'supplier_code',
    ''
  )
$$;

create or replace function public.can_read_pipeline() returns boolean
language sql stable as $$
  select public.has_role('admin', 'migration_engineer', 'finance_controller', 'inventory_manager')
$$;

create or replace function public.can_write_pipeline() returns boolean
language sql stable as $$
  select public.has_role('admin', 'migration_engineer')
$$;

create or replace function public.can_read_finance() returns boolean
language sql stable as $$
  select public.has_role('admin', 'finance_controller')
$$;

create or replace function public.can_read_inventory() returns boolean
language sql stable as $$
  select public.has_role('admin', 'inventory_manager', 'migration_engineer')
$$;

-- -------------------------------------------------- enable RLS on tables ---
do $rls$
declare
  t text;
  pipeline_tables text[] := array[
    'migration_batches', 'migration_stages', 'ingested_files',
    'column_profiles', 'validation_findings', 'review_queue',
    'field_mappings', 'reconciliation_reports'
  ];
begin
  foreach t in array pipeline_tables loop
    execute format('alter table public.%I enable row level security', t);
  end loop;

  foreach t in array array[
    'roles', 'permissions', 'role_permissions', 'app_users', 'audit_log',
    'categories', 'uom', 'tax_codes', 'suppliers', 'customers', 'items',
    'supplier_catalog', 'chart_of_accounts', 'gl_balances', 'purchase_orders',
    'invoices', 'inventory_stock', 'employees_payroll_summary'
  ] loop
    execute format('alter table public.%I enable row level security', t);
  end loop;
end
$rls$;

-- --------------------------------------------------- RBAC reference tables ---
drop policy if exists roles_read on public.roles;
create policy roles_read on public.roles
  for select to authenticated using (true);

drop policy if exists permissions_read on public.permissions;
create policy permissions_read on public.permissions
  for select to authenticated using (true);

drop policy if exists role_permissions_read on public.role_permissions;
create policy role_permissions_read on public.role_permissions
  for select to authenticated using (true);

drop policy if exists app_users_self_read on public.app_users;
create policy app_users_self_read on public.app_users
  for select to authenticated
  using (id = auth.uid() or public.has_role('admin'));

drop policy if exists app_users_admin_write on public.app_users;
create policy app_users_admin_write on public.app_users
  for all to authenticated
  using (public.has_role('admin')) with check (public.has_role('admin'));

-- -------------------------------------------------------- pipeline tables ---
do $pipeline$
declare
  t text;
begin
  foreach t in array array[
    'migration_batches', 'migration_stages', 'ingested_files',
    'column_profiles', 'validation_findings', 'review_queue',
    'field_mappings', 'reconciliation_reports'
  ] loop
    execute format('drop policy if exists %I_read on public.%I', t, t);
    execute format(
      'create policy %I_read on public.%I for select to authenticated using (public.can_read_pipeline())',
      t, t
    );
  end loop;

  -- Only engineers and admins may create or change pipeline rows.
  foreach t in array array[
    'migration_batches', 'migration_stages', 'ingested_files',
    'column_profiles', 'validation_findings', 'review_queue',
    'field_mappings', 'reconciliation_reports'
  ] loop
    execute format('drop policy if exists %I_write on public.%I', t, t);
    execute format(
      'create policy %I_write on public.%I for all to authenticated
         using (public.can_write_pipeline()) with check (public.can_write_pipeline())',
      t, t
    );
  end loop;
end
$pipeline$;

-- Finance and inventory reviewers also need to decide on queue items.
drop policy if exists review_queue_reviewer_update on public.review_queue;
create policy review_queue_reviewer_update on public.review_queue
  for update to authenticated
  using (public.has_role('finance_controller', 'inventory_manager'))
  with check (public.has_role('finance_controller', 'inventory_manager'));

drop policy if exists validation_findings_reviewer_update on public.validation_findings;
create policy validation_findings_reviewer_update on public.validation_findings
  for update to authenticated
  using (public.has_role('finance_controller', 'inventory_manager'))
  with check (public.has_role('finance_controller', 'inventory_manager'));

-- ------------------------------------------------------------ audit log ----
-- Readable by the three internal audit-facing roles. Nobody authenticated may
-- insert, update or delete: the trigger blocks updates, and no INSERT policy
-- exists, so writes only happen through service_role.
drop policy if exists audit_log_read on public.audit_log;
create policy audit_log_read on public.audit_log
  for select to authenticated
  using (public.has_role('admin', 'migration_engineer', 'finance_controller'));

-- --------------------------------------------------------- reference data --
do $refdata$
declare
  t text;
begin
  foreach t in array array['categories', 'uom', 'tax_codes'] loop
    execute format('drop policy if exists %I_read on public.%I', t, t);
    execute format(
      'create policy %I_read on public.%I for select to authenticated
         using (public.can_read_pipeline() or public.can_read_finance() or public.can_read_inventory())',
      t, t
    );
  end loop;
end
$refdata$;

-- ----------------------------------------------------------- suppliers -----
-- Internal roles see every supplier. A supplier user sees exactly one row:
-- their own, matched on the supplier_code claim in their JWT.
drop policy if exists suppliers_internal_read on public.suppliers;
create policy suppliers_internal_read on public.suppliers
  for select to authenticated
  using (public.has_role('admin', 'migration_engineer', 'finance_controller', 'inventory_manager'));

drop policy if exists suppliers_self_read on public.suppliers;
create policy suppliers_self_read on public.suppliers
  for select to authenticated
  using (public.has_role('supplier') and supplier_code = public.app_supplier_code());

-- ----------------------------------------------------------- customers -----
-- Customer master data is commercially sensitive. Suppliers are excluded
-- entirely (no master data at all), and so is inventory: the inventory role
-- owns items and stock, not who the company sells to.
drop policy if exists customers_internal_read on public.customers;
create policy customers_internal_read on public.customers
  for select to authenticated
  using (public.has_role('admin', 'migration_engineer', 'finance_controller'));

-- --------------------------------------------------------------- items -----
drop policy if exists items_internal_read on public.items;
create policy items_internal_read on public.items
  for select to authenticated
  using (public.can_read_pipeline() or public.can_read_finance() or public.can_read_inventory());

drop policy if exists items_inventory_write on public.items;
create policy items_inventory_write on public.items
  for all to authenticated
  using (public.has_role('admin', 'inventory_manager'))
  with check (public.has_role('admin', 'inventory_manager'));

-- A supplier sees only the items on their own catalogue.
drop policy if exists supplier_catalog_self_read on public.supplier_catalog;
create policy supplier_catalog_self_read on public.supplier_catalog
  for select to authenticated
  using (
    (public.has_role('supplier') and supplier_code = public.app_supplier_code())
    or public.can_read_pipeline()
    or public.can_read_inventory()
  );

-- ------------------------------------------------------------- finance -----
do $finance$
declare
  t text;
begin
  foreach t in array array['chart_of_accounts', 'gl_balances', 'invoices'] loop
    execute format('drop policy if exists %I_finance_read on public.%I', t, t);
    execute format(
      'create policy %I_finance_read on public.%I for select to authenticated using (public.can_read_finance())',
      t, t
    );
  end loop;
end
$finance$;

-- Payroll is stricter still: finance and admin only, and the API masks the
-- salary columns for anyone without payroll:read.
drop policy if exists payroll_finance_read on public.employees_payroll_summary;
create policy payroll_finance_read on public.employees_payroll_summary
  for select to authenticated using (public.can_read_finance());

-- --------------------------------------------------- purchase orders -------
-- Internal roles see all POs. A supplier sees only their own.
drop policy if exists purchase_orders_internal_read on public.purchase_orders;
create policy purchase_orders_internal_read on public.purchase_orders
  for select to authenticated
  using (public.can_read_pipeline() or public.can_read_finance() or public.can_read_inventory());

drop policy if exists purchase_orders_supplier_read on public.purchase_orders;
create policy purchase_orders_supplier_read on public.purchase_orders
  for select to authenticated
  using (public.has_role('supplier') and supplier_code = public.app_supplier_code());

-- ------------------------------------------------------------ inventory ----
drop policy if exists inventory_stock_read on public.inventory_stock;
create policy inventory_stock_read on public.inventory_stock
  for select to authenticated
  using (public.can_read_inventory() or public.can_read_finance());

-- --------------------------------------------------------------- grants ----
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
    execute format('grant select, insert, update, delete on public.%I to service_role', t);
    execute format('grant select on public.%I to authenticated', t);
  end loop;

  foreach t in array array['review_queue', 'validation_findings', 'field_mappings', 'app_users', 'items'] loop
    execute format('grant update on public.%I to authenticated', t);
  end loop;

  execute 'grant insert on public.audit_log to service_role';
  execute 'grant usage on schema public to authenticated, anon, service_role';
  execute 'grant usage on schema staging to service_role';
  execute 'grant select, insert, update, delete on all tables in schema staging to service_role';
end
$grants$;
