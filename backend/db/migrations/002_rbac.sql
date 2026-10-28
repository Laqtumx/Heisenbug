-- 002: RBAC — users, roles, permissions and the role/permission matrix.
--
-- `app_users` is the application's user directory. On Supabase it is kept in
-- step with auth.users by trigger; locally it is the source of truth and holds
-- a bcrypt password hash. Either way the authorisation decision comes from the
-- JWT claim, never from anything the client can send in a body.

create table if not exists public.roles (
  role            text primary key,
  label           text not null,
  description     text not null default '',
  is_external     boolean not null default false,
  created_at      timestamptz not null default now()
);

insert into public.roles (role, label, description, is_external) values
  ('admin',              'Administrator',        'Full access to every module, including rollback and RBAC.', false),
  ('migration_engineer', 'Migration Engineer',   'Runs ingest, profile, validate, clean and load. No payroll data.', false),
  ('finance_controller', 'Finance Controller',   'Owns finance validation, approvals and go-live sign-off.', false),
  ('inventory_manager',  'Inventory Manager',    'Owns items, stock and warehouse data.', false),
  ('supplier',           'Supplier (external)',  'Sees only their own purchase orders and catalogue. No finance data.', true)
on conflict (role) do update
  set label = excluded.label,
      description = excluded.description,
      is_external = excluded.is_external;

create table if not exists public.permissions (
  permission  text primary key,
  module      text not null,
  description text not null default ''
);

insert into public.permissions (permission, module, description) values
  ('batch:read',        'pipeline',  'Read batches, stages and pipeline status.'),
  ('batch:create',      'pipeline',  'Create a migration batch.'),
  ('batch:delete',      'pipeline',  'Delete a migration batch.'),
  ('ingest:run',        'pipeline',  'Upload and ingest source files.'),
  ('profile:run',       'pipeline',  'Run column profiling.'),
  ('validate:run',      'pipeline',  'Run the validation rule engine.'),
  ('clean:run',         'pipeline',  'Apply automatic cleaning.'),
  ('map:run',           'pipeline',  'Run AI column mapping.'),
  ('dryrun:run',        'pipeline',  'Run the sandbox dry run.'),
  ('load:run',          'pipeline',  'Load into the target ERP schema.'),
  ('rollback:run',      'pipeline',  'Roll back a loaded batch.'),
  ('finding:read',      'quality',   'Read validation findings.'),
  ('review:decide',     'quality',   'Approve or reject records in the review queue.'),
  ('report:read',       'reporting', 'Read reconciliation and health reports.'),
  ('report:signoff',    'reporting', 'Sign off go-live readiness.'),
  ('report:export',     'reporting', 'Export reports as PDF or CSV.'),
  ('audit:read',        'audit',     'Read the audit log.'),
  ('finance:read',      'finance',   'Read finance data and dashboards.'),
  ('finance:approve',   'finance',   'Approve finance findings and totals.'),
  ('payroll:read',      'finance',   'Read employee payroll summaries (sensitive).'),
  ('inventory:read',    'inventory', 'Read items, stock and demand insights.'),
  ('inventory:write',   'inventory', 'Maintain items, stock and UoM.'),
  ('supplier:self',     'supplier',  'Read own supplier record, catalogue and purchase orders.'),
  ('admin:rbac',        'admin',     'Manage users, roles and permissions.')
on conflict (permission) do update
  set module = excluded.module,
      description = excluded.description;

create table if not exists public.role_permissions (
  role        text not null references public.roles(role) on delete cascade,
  permission  text not null references public.permissions(permission) on delete cascade,
  primary key (role, permission)
);

-- Role/permission matrix. Re-running this file re-derives the matrix, so it
-- stays the single source of truth for what each role can do.
delete from public.role_permissions;

insert into public.role_permissions (role, permission)
select 'admin', permission from public.permissions;

insert into public.role_permissions (role, permission) values
  ('migration_engineer', 'batch:read'),
  ('migration_engineer', 'batch:create'),
  ('migration_engineer', 'ingest:run'),
  ('migration_engineer', 'profile:run'),
  ('migration_engineer', 'validate:run'),
  ('migration_engineer', 'clean:run'),
  ('migration_engineer', 'map:run'),
  ('migration_engineer', 'dryrun:run'),
  ('migration_engineer', 'load:run'),
  ('migration_engineer', 'rollback:run'),
  ('migration_engineer', 'finding:read'),
  ('migration_engineer', 'review:decide'),
  ('migration_engineer', 'report:read'),
  ('migration_engineer', 'report:export'),
  ('migration_engineer', 'audit:read'),
  ('migration_engineer', 'inventory:read'),
  ('migration_engineer', 'supplier:self'),
  ('finance_controller', 'batch:read'),
  ('finance_controller', 'finding:read'),
  ('finance_controller', 'review:decide'),
  ('finance_controller', 'report:read'),
  ('finance_controller', 'report:signoff'),
  ('finance_controller', 'report:export'),
  ('finance_controller', 'audit:read'),
  ('finance_controller', 'finance:read'),
  ('finance_controller', 'finance:approve'),
  ('finance_controller', 'payroll:read'),
  ('inventory_manager', 'batch:read'),
  ('inventory_manager', 'finding:read'),
  ('inventory_manager', 'review:decide'),
  ('inventory_manager', 'report:read'),
  ('inventory_manager', 'report:export'),
  ('inventory_manager', 'inventory:read'),
  ('inventory_manager', 'inventory:write'),
  ('supplier', 'supplier:self'),
  ('supplier', 'report:read')
on conflict do nothing;

create table if not exists public.app_users (
  id              uuid primary key default gen_random_uuid(),
  email           text not null unique,
  full_name       text not null default '',
  password_hash   text,
  role            text not null references public.roles(role),
  supplier_id     uuid,
  is_active       boolean not null default true,
  last_login_at   timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index if not exists app_users_role_idx on public.app_users (role);
create index if not exists app_users_supplier_idx on public.app_users (supplier_id);

drop trigger if exists app_users_touch on public.app_users;
create trigger app_users_touch before update on public.app_users
  for each row execute function public.touch_updated_at();
