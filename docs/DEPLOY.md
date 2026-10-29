# Deploying CleanSlate

The app runs from a single repository. The frontend goes to Vercel and the API
goes to a container host. Postgres and Mongo are managed services, because a
host's local disk is not a database and the migration data must outlive a
redeploy.

Total time: about 30 minutes. You need four accounts, all with a free tier.

## What you need before you start

- A Supabase project (Postgres, with the `auth` schema)
- A MongoDB Atlas cluster (free M0 is enough)
- A Render account (or Railway or Fly, the Dockerfile works anywhere)
- A Vercel account

## Step 1. Postgres on Supabase

1. Create a project. Save the database password.
2. Open Project settings, then Database, then Connection string, and copy the
   pooled URI. It looks like
   `postgresql://postgres.xxxx:PASSWORD@aws-0-region.pooler.supabase.com:6543/postgres`.
3. That value is your `DATABASE_URL`. The app accepts the bare `postgresql://`
   form and rewrites it for psycopg.
4. Leave `RLS_DATABASE_URL` blank. Supabase already separates the API roles.

The migrations create the `auth` contract themselves only when
`ENVIRONMENT=local`. On Supabase that schema already exists, so set
`ENVIRONMENT=production` and the local shims are skipped.

## Step 2. Mongo on Atlas

1. Create a free M0 cluster.
2. Add a database user and allow access from anywhere (`0.0.0.0/0`) or from the
   container host's egress range.
3. Copy the connection string. That value is your `MONGO_URL`.

## Step 3. API on Render

1. Push this repository to GitHub.
2. In Render, choose New, then Blueprint, and point it at the repo. Render reads
   `render.yaml` and asks for the values marked `sync: false`.
3. Set these environment variables:

   | Variable | Value |
   | --- | --- |
   | `DATABASE_URL` | the Supabase pooled URI |
   | `MONGO_URL` | the Atlas URI |
   | `FIELD_ENCRYPTION_KEY` | generate once, see below |
   | `BLIND_INDEX_KEY` | generate once, see below |
   | `JWT_SECRET` | Render generates this |
   | `CORS_ORIGINS` | the Vercel URL, set after step 5 |
   | `ENVIRONMENT` | `production` |

4. Generate the two keys and keep them. Changing them later makes existing
   encrypted columns unreadable.

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

5. Deploy. The health check hits `/health` and should report `"ok": true`.

To build and run the API locally the same way, from the repository root:

```bash
docker build -f backend/Dockerfile -t cleanslate-api .
docker run --rm -p 8000:8000 --env-file .env cleanslate-api
```

## Step 4. Run the migrations and seed the demo data

The migrations run automatically on API start. The demo data does not, because
generating it is a one-off step. Run it against the hosted database once, from
your machine:

```bash
cd backend
export DATABASE_URL="<the Supabase URI>"
export MONGO_URL="<the Atlas URI>"
export ENVIRONMENT=production
export FIELD_ENCRYPTION_KEY="<the key from step 3>"
export BLIND_INDEX_KEY="<the key from step 3>"
.venv/bin/python ../scripts/generate_demo_data.py
.venv/bin/python ../scripts/seed.py
```

The seed creates the five demo users and runs one end-to-end migration so the
dashboards have data on first load.

## Step 5. Frontend on Vercel

1. Import the repository. Set the root directory to `frontend`.
2. Add one environment variable before the first build:

   | Variable | Value |
   | --- | --- |
   | `NEXT_PUBLIC_API_BASE` | the Render API URL, for example `https://cleanslate-api.onrender.com` |

3. Deploy.

The API base is read at build time, because `next.config.mjs` bakes the proxy
rewrite into the build. If you set it after a build, the deployed frontend still
points at `127.0.0.1:8000` and every page fails. Set it first, then deploy.

## Step 6. Close the loop on CORS

1. Copy the Vercel URL.
2. Set `CORS_ORIGINS` on Render to that URL and redeploy the API.

The browser talks to the Vercel origin and the rewrite proxies to the API, so
CORS only matters if you call the API directly from another origin. Setting it
is still worth doing.

## Demo accounts

Password for all of them: `cleanslate`

| Email | Role |
| --- | --- |
| `admin@cleanslate.test` | Admin |
| `engineer@cleanslate.test` | Migration Engineer |
| `finance@cleanslate.test` | Finance Controller |
| `inventory@cleanslate.test` | Inventory Manager |
| `supplier@cleanslate.test` | Supplier |

Change these before you share the link with anyone outside the judging panel.

## Check the deploy

```bash
curl -s https://<api-host>/health
curl -s -o /dev/null -w "%{http_code}\n" https://<vercel-host>/login
curl -s -X POST https://<vercel-host>/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@cleanslate.test","password":"cleanslate"}'
```

You want `"ok": true`, then `200`, then `200`.

## Single-server alternative

If you would rather have one URL and one place to debug, run Postgres, Mongo and
both apps on one small VPS behind Caddy. It is less setup than four accounts and
it will not fail at 2am before a deadline. The trade-off is that the VPS is a
real machine you have to patch.

## Known gaps

These are not deploy problems, but do not claim them in a demo:

- The LLM mapping and explanation step is not built. The `map` stage shows in the
  wizard and stays pending. No key is needed, because nothing calls an LLM.
- There is no separate verify or sign-off endpoint. Reconciliation and the health
  score come from the load stage.
- The Postman collection and the demo script are not in the repository. The
  diagrams in `README.md` are the current source.
