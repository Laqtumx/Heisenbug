-- 004: target ERP schema.
--
-- Every table carries (batch_id, source_ref) so a rollback is a scoped delete
-- and every loaded row can be traced back to the legacy record it came from.
--
-- Sensitive columns (tax identifiers, contact details, salary) are stored as
-- application-encrypted ciphertext. `*_hash` columns hold a keyed blind index
-- so duplicates can still be detected without decrypting the column.

-- --------------------------------------------------------------- reference --
create table if not exists public.categories (
  id             uuid primary key default gen_random_uuid(),
  category_code  text not null,
  name           text not null,
  parent_code    text,
  is_active      boolean not null default true,
  batch_id       uuid references public.migration_batches(id) on delete cascade,
  source_ref     text,
  loaded_at      timestamptz not null default now(),
  unique (category_code, batch_id)
);

create table if not exists public.uom (
  id                uuid primary key default gen_random_uuid(),
  uom_code          text not null,
  name              text not null,
  base_uom_code     text,
  conversion_factor numeric(20,6) not null default 1,
  is_active         boolean not null default true,
  batch_id          uuid references public.migration_batches(id) on delete cascade,
  source_ref        text,
  loaded_at         timestamptz not null default now(),
  unique (uom_code, batch_id)
);

create table if not exists public.tax_codes (
  id             uuid primary key default gen_random_uuid(),
  tax_code       text not null,
  description    text not null default '',
  rate           numeric(9,4) not null default 0,
  tax_type       text not null default 'GST',
  is_retired     boolean not null default false,
  effective_from date,
  effective_to   date,
  batch_id       uuid references public.migration_batches(id) on delete cascade,
  source_ref     text,
  loaded_at      timestamptz not null default now(),
  unique (tax_code, batch_id)
);

-- ------------------------------------------------------------ master data --
create table if not exists public.suppliers (
  id                 uuid primary key default gen_random_uuid(),
  supplier_code      text not null,
  name               text not null,
  gstin_encrypted    text,
  gstin_hash         text,
  pan_encrypted      text,
  pan_hash           text,
  email_encrypted    text,
  email_hash         text,
  phone_encrypted    text,
  phone_hash         text,
  pincode            text,
  city               text,
  state              text,
  country            text default 'IN',
  payment_terms      text,
  currency           text not null default 'INR',
  is_active          boolean not null default true,
  is_archived        boolean not null default false,
  last_activity_date date,
  batch_id           uuid references public.migration_batches(id) on delete cascade,
  source_ref         text,
  loaded_at          timestamptz not null default now(),
  unique (supplier_code, batch_id)
);

create index if not exists suppliers_batch_idx on public.suppliers (batch_id);
create index if not exists suppliers_gstin_hash_idx on public.suppliers (gstin_hash);
create index if not exists suppliers_pan_hash_idx on public.suppliers (pan_hash);
create index if not exists suppliers_name_idx on public.suppliers (name);

create table if not exists public.customers (
  id                 uuid primary key default gen_random_uuid(),
  customer_code      text not null,
  name               text not null,
  gstin_encrypted    text,
  gstin_hash         text,
  pan_encrypted      text,
  pan_hash           text,
  email_encrypted    text,
  email_hash         text,
  phone_encrypted    text,
  phone_hash         text,
  pincode            text,
  city               text,
  state              text,
  country            text default 'IN',
  currency           text not null default 'INR',
  credit_limit       numeric(20,2) not null default 0,
  is_active          boolean not null default true,
  is_archived        boolean not null default false,
  last_activity_date date,
  batch_id           uuid references public.migration_batches(id) on delete cascade,
  source_ref         text,
  loaded_at          timestamptz not null default now(),
  unique (customer_code, batch_id)
);

create index if not exists customers_batch_idx on public.customers (batch_id);
create index if not exists customers_gstin_hash_idx on public.customers (gstin_hash);
create index if not exists customers_name_idx on public.customers (name);

create table if not exists public.items (
  id              uuid primary key default gen_random_uuid(),
  item_code       text not null,
  name            text not null,
  category_code   text,
  uom_code        text,
  standard_cost   numeric(20,4) not null default 0,
  list_price      numeric(20,4) not null default 0,
  reorder_level   numeric(20,4) not null default 0,
  is_active       boolean not null default true,
  is_archived     boolean not null default false,
  last_activity_date date,
  batch_id        uuid references public.migration_batches(id) on delete cascade,
  source_ref      text,
  loaded_at       timestamptz not null default now(),
  unique (item_code, batch_id)
);

create index if not exists items_batch_idx on public.items (batch_id);
create index if not exists items_category_idx on public.items (category_code);

-- --------------------------------------------------------------- finance ---
create table if not exists public.chart_of_accounts (
  id                  uuid primary key default gen_random_uuid(),
  account_code        text not null,
  account_name        text not null,
  account_type        text not null,
  parent_account_code text,
  currency            text not null default 'INR',
  is_active           boolean not null default true,
  batch_id            uuid references public.migration_batches(id) on delete cascade,
  source_ref          text,
  loaded_at           timestamptz not null default now(),
  unique (account_code, batch_id)
);

create table if not exists public.gl_balances (
  id            uuid primary key default gen_random_uuid(),
  account_code  text not null,
  fiscal_year   integer not null,
  period        integer not null,
  debit         numeric(20,2) not null default 0,
  credit        numeric(20,2) not null default 0,
  currency      text not null default 'INR',
  batch_id      uuid references public.migration_batches(id) on delete cascade,
  source_ref    text,
  loaded_at     timestamptz not null default now()
);

create index if not exists gl_balances_batch_idx on public.gl_balances (batch_id);
create index if not exists gl_balances_account_idx on public.gl_balances (account_code, fiscal_year, period);

create table if not exists public.purchase_orders (
  id            uuid primary key default gen_random_uuid(),
  po_number     text not null,
  supplier_code text,
  order_date    date,
  currency      text not null default 'INR',
  total_amount  numeric(20,2) not null default 0,
  status        text not null default 'open',
  batch_id      uuid references public.migration_batches(id) on delete cascade,
  source_ref    text,
  loaded_at     timestamptz not null default now(),
  unique (po_number, batch_id)
);

create index if not exists purchase_orders_batch_idx on public.purchase_orders (batch_id);
create index if not exists purchase_orders_supplier_idx on public.purchase_orders (supplier_code);

create table if not exists public.invoices (
  id             uuid primary key default gen_random_uuid(),
  invoice_number text not null,
  customer_code  text,
  invoice_date   date,
  due_date       date,
  currency       text not null default 'INR',
  subtotal       numeric(20,2) not null default 0,
  tax_amount     numeric(20,2) not null default 0,
  total_amount   numeric(20,2) not null default 0,
  tax_code       text,
  status         text not null default 'open',
  batch_id       uuid references public.migration_batches(id) on delete cascade,
  source_ref     text,
  loaded_at      timestamptz not null default now(),
  unique (invoice_number, batch_id)
);

create index if not exists invoices_batch_idx on public.invoices (batch_id);
create index if not exists invoices_customer_idx on public.invoices (customer_code);

-- ------------------------------------------------------------- inventory ---
create table if not exists public.inventory_stock (
  id             uuid primary key default gen_random_uuid(),
  item_code      text not null,
  warehouse_code text not null default 'WH01',
  uom_code       text,
  qty_on_hand    numeric(20,4) not null default 0,
  batch_id       uuid references public.migration_batches(id) on delete cascade,
  source_ref     text,
  loaded_at      timestamptz not null default now(),
  unique (item_code, warehouse_code, batch_id)
);

create index if not exists inventory_stock_batch_idx on public.inventory_stock (batch_id);

-- --------------------------------------------------------------- payroll ---
create table if not exists public.employees_payroll_summary (
  id                    uuid primary key default gen_random_uuid(),
  employee_code         text not null,
  employee_name         text not null,
  department            text,
  fiscal_year           integer,
  gross_salary_encrypted text,
  net_salary_encrypted   text,
  currency              text not null default 'INR',
  batch_id              uuid references public.migration_batches(id) on delete cascade,
  source_ref            text,
  loaded_at             timestamptz not null default now(),
  unique (employee_code, fiscal_year, batch_id)
);

create index if not exists payroll_batch_idx on public.employees_payroll_summary (batch_id);

-- ------------------------------------------------------------ dry-run bin --
-- The dry run writes here. Same shape as the target tables, no batch semantics.
create schema if not exists staging;

do $staging$
declare
  t text;
begin
  foreach t in array array[
    'categories', 'uom', 'tax_codes', 'suppliers', 'customers', 'items',
    'chart_of_accounts', 'gl_balances', 'purchase_orders', 'invoices',
    'inventory_stock', 'employees_payroll_summary'
  ] loop
    if not exists (
      select 1 from information_schema.tables
      where table_schema = 'staging' and table_name = t
    ) then
      execute format(
        'create table staging.%I (like public.%I including defaults including constraints including storage)',
        t, t
      );
      execute format('create index if not exists %I on staging.%I (batch_id)', t || '_batch_idx', t);
    end if;
  end loop;
end
$staging$;
