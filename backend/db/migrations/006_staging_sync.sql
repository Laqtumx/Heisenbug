-- Keep the staging schema in step with the target schema.
--
-- The dry run writes into staging and the real load writes into public, so the
-- two must have the same shape or the dry run proves nothing about the load.
-- Migration 004 built the staging tables from a hardcoded list of names, and
-- supplier_catalog was added later in 004b, so staging silently fell behind and
-- the dry run failed on a table the real load was happy with.
--
-- This migration derives the staging tables from the target tables themselves
-- rather than from a list. A table with a batch_id column is a per-batch table
-- and needs a staging twin; anything added later is picked up automatically the
-- next time migrations run.

do $staging_sync$
declare
  t record;
begin
  for t in
    select c.table_name
      from information_schema.columns c
      join information_schema.tables tb
        on tb.table_schema = c.table_schema
       and tb.table_name = c.table_name
       and tb.table_type = 'BASE TABLE'
     where c.table_schema = 'public'
       and c.column_name = 'batch_id'
     order by c.table_name
  loop
    if not exists (
      select 1 from information_schema.tables
       where table_schema = 'staging' and table_name = t.table_name
    ) then
      execute format(
        'create table staging.%I (like public.%I including defaults including constraints including storage)',
        t.table_name, t.table_name
      );
    end if;

    -- The index is created outside the existence check above so a table that
    -- already existed without one still gets it.
    execute format(
      'create index if not exists %I on staging.%I (batch_id)',
      t.table_name || '_batch_idx', t.table_name
    );
  end loop;
end
$staging_sync$;

grant usage on schema staging to service_role;
grant select, insert, update, delete on all tables in schema staging to service_role;

-- Copy the unique constraints across as unique indexes.
--
-- "like ... including constraints" copies NOT NULL and CHECK but not UNIQUE, so
-- staging was missing every unique key the target has. That made the dry run
-- weaker than the load: it accepted duplicate business keys, reported the batch
-- as reconciled, and the real load then failed on the very key the dry run had
-- waved through. A dry run that is more permissive than the load proves nothing.
--
-- A unique index enforces the same thing a unique constraint does, so the dry
-- run now rejects exactly what the load would reject.
do $staging_unique$
declare
  con record;
  cols text;
  index_name text;
begin
  for con in
    select tc.table_name, tc.constraint_name,
           string_agg(kcu.column_name, ', ' order by kcu.ordinal_position) as columns
      from information_schema.table_constraints tc
      join information_schema.key_column_usage kcu
        on kcu.constraint_name = tc.constraint_name
       and kcu.table_schema = tc.table_schema
     where tc.table_schema = 'public'
       and tc.constraint_type = 'UNIQUE'
       and exists (
         select 1 from information_schema.tables st
          where st.table_schema = 'staging' and st.table_name = tc.table_name
       )
     group by tc.table_name, tc.constraint_name
  loop
    cols := con.columns;
    index_name := left(con.table_name || '_' || replace(cols, ', ', '_') || '_uniq', 63);
    execute format(
      'create unique index if not exists %I on staging.%I (%s)',
      index_name, con.table_name, cols
    );
  end loop;
end
$staging_unique$;
