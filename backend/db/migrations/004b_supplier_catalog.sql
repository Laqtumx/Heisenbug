-- Supplier catalogue: which items a supplier can quote for.
-- Exists so the external supplier role has something concrete to read, and so
-- the RLS demo can prove a supplier sees their own catalogue and nothing else.

create table if not exists public.supplier_catalog (
  id            uuid primary key default gen_random_uuid(),
  supplier_code text not null,
  item_code     text not null,
  supplier_sku  text,
  unit_price    numeric(20,4) not null default 0,
  currency      text not null default 'INR',
  lead_time_days integer,
  min_order_qty numeric(20,4) not null default 1,
  is_active     boolean not null default true,
  batch_id      uuid references public.migration_batches(id) on delete cascade,
  source_ref    text,
  loaded_at     timestamptz not null default now(),
  unique (supplier_code, item_code, batch_id)
);

create index if not exists supplier_catalog_supplier_idx on public.supplier_catalog (supplier_code);
