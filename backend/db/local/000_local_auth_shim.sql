-- Local development compatibility shim.
--
-- Supabase ships the `auth` schema plus auth.uid() / auth.jwt() out of the box.
-- This file recreates exactly that contract on a plain Postgres instance so the
-- RLS policies in ../migrations/ behave identically locally and on Supabase.
--
-- Every statement is conditional, so running this against a real Supabase
-- project is a no-op and never overwrites the platform implementation.

create schema if not exists auth;

do $shim$
begin
  if not exists (
    select 1 from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'auth' and p.proname = 'jwt'
  ) then
    execute $sql$
      create function auth.jwt() returns jsonb
      language sql stable as $body$
        select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
      $body$;
    $sql$;
  end if;

  if not exists (
    select 1 from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'auth' and p.proname = 'uid'
  ) then
    execute $sql$
      create function auth.uid() returns uuid
      language sql stable as $body$
        select nullif(auth.jwt() ->> 'sub', '')::uuid
      $body$;
    $sql$;
  end if;

  if not exists (
    select 1 from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'auth' and p.proname = 'role'
  ) then
    execute $sql$
      create function auth.role() returns text
      language sql stable as $body$
        select coalesce(nullif(auth.jwt() ->> 'role', ''), 'anon')
      $body$;
    $sql$;
  end if;

  if not exists (
    select 1 from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'auth' and p.proname = 'email'
  ) then
    execute $sql$
      create function auth.email() returns text
      language sql stable as $body$
        select nullif(auth.jwt() ->> 'email', '')
      $body$;
    $sql$;
  end if;
end
$shim$;
