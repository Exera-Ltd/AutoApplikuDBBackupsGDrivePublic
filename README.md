# AutoApplikuDBBackupsGDrive

Centralized, hourly backups of **every** [Appliku](https://appliku.com)-hosted
Postgres database in an account to **Google Drive**, with an email summary and a
guarded manual **restore** workflow.

It runs entirely in **GitHub Actions** — nothing to install on the servers.
Appliku's own daily **Local** backups (keep‑7) can stay enabled as the on‑server
layer.

> **Scope:** databases only. A GitHub runner can reach Postgres over public
> connection URLs but cannot reach Appliku media volumes, so media is out of
> scope here.

All account-specific values (team slug, email addresses, SMTP host) are supplied
at runtime via **GitHub Actions secrets and variables** — nothing identifying is
committed to this repository.

> **Privacy on a public repo.** App names are fetched live from the Appliku API,
> so they must not surface on this public repo's world-readable Actions pages.
> Every database is addressed on the public surface (job matrix, **job names**,
> **logs**, **artifacts**) by an opaque, stable id `db-<hash>`. Real app names
> appear only in **private** channels: your Google Drive folder names and the
> summary email (which lists each app next to its id). See *Anonymization* below.

---

## How it works

```
backup.yml (cron: hourly + manual)
 ├─ discover  → scripts/discover.py queries the Appliku API and emits a job
 │             matrix of every external Postgres DB by app slug (the db_url is
 │             deliberately NOT in the matrix — a masked value would void the
 │             job output; each leg re-resolves its own url at runtime)
 ├─ backup    → matrix, max-parallel 4, fail-fast off. For each DB:
 │             resolve url → pg_dump | gzip → size/integrity check → rclone copy to
 │             gdrive:ApplikuBackups/<app>/db_<app>_<UTCstamp>.sql.gz →
 │             prune to newest 48. Writes a status_<app>.json artifact.
 └─ notify    → if: always(). Merges status artifacts, builds an HTML table,
               emails the configured recipient per the volume policy below.

restore.yml (manual only) — dry run by default; guarded real restore.
keepalive.yml (weekly) — keeps the hourly schedule from auto-disabling.
```

### Discovery

The `appliku` CLI's `datastores list` is unreliable, so we call the REST API
directly:

- `GET /api/team/<TEAM>/applications/list/` → all applications
- `GET /api/team/<TEAM>/applications/<id>/datastores` → keep entries with
  `kind == "database"` **and** `allow_external_connections == true`; use
  `properties.connection_url`.

Auth header: `Authorization: Token <APPLIKU_TOKEN>`. `<TEAM>` comes from the
`APPLIKU_TEAM` secret.

An app with **two** external databases gets slugged folders
`<app>_<datastoreId>`; every other app uses its bare name. If one server runs a
newer Postgres major (e.g. PG17) than the rest (PG16), the workflows install
**postgresql-client-17** so `pg_dump` is always ≥ the server (a newer `pg_dump`
can dump older servers, never the reverse).

New projects are picked up automatically every run; nothing to edit when you add
an app. To skip an app, add its name (or slug) to `config/exclude.txt`.

### Anonymization (public-repo privacy)

`discover.py` derives a stable opaque id `db-<hash>` (salted SHA-256 of the app
slug) for each database and that is the **only** thing placed on GitHub's public
surface:

- the job **matrix** carries `{"id": "db-..."}` — no app name, no db_url;
- each backup **job name** is `backup-db-...`;
- each backup leg resolves its real slug + db_url from the id at runtime and
  immediately `::add-mask::`s **both**, so neither can appear in the public log;
- status **artifacts** are named `status-db-...` and contain only the id.

Real app names are used only where they're private: the **Google Drive** folder
(`ApplikuBackups/<app>/…`) and the **summary email**, which shows each app
alongside its id so you can map an id back to a client when restoring.

Because the id is salted with a secret (`ANON_SALT`, or the API token by
default), outsiders can't reverse the hash to recover your (otherwise guessable)
app names.

---

## Required GitHub secrets

Add these under **Settings → Secrets and variables → Actions → Secrets**.
Do **not** commit them.

| Secret | Value |
| --- | --- |
| `APPLIKU_TOKEN` | The Appliku API token (`Authorization: Token <...>`). |
| `APPLIKU_TEAM` | Your Appliku team slug (the `<TEAM>` in the API paths above). |
| `ANON_SALT` | *(optional)* Salt for the opaque `db-<hash>` ids. Defaults to `APPLIKU_TOKEN`; set a dedicated random value to keep ids stable across token rotations. |
| `RCLONE_CONF_B64` | base64 of an `rclone.conf` with a `[gdrive]` drive remote (see below). |
| `SMTP_USER` | SMTP username for the summary email account. |
| `SMTP_PASS` | SMTP password / app password. |
| `MAIL_FROM` | Envelope From address (e.g. `backups@example.com`). |
| `MAIL_TO` | Recipient address for the summary / restore emails. |

Required **repository variables** (Settings → Variables):

| Variable | Example | Meaning |
| --- | --- | --- |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server host. |
| `SMTP_PORT` | `587` | SMTP port (optional, defaults to `587`). |

Optional **repository variables** to tune email volume:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALWAYS_EMAIL` | `false` | `true` = email every run (24/day). `false` = email on any failure + one daily all‑success digest. |
| `DIGEST_HOUR` | `6` | UTC hour of the daily all‑success digest. |

### Set the secrets with the GitHub CLI

Replace `<owner>/<repo>` with your repository.

```bash
gh secret set APPLIKU_TOKEN   -R <owner>/<repo>   # paste the Appliku API token
gh secret set APPLIKU_TEAM    -R <owner>/<repo> -b '<your-team-slug>'
gh secret set ANON_SALT       -R <owner>/<repo> -b "$(openssl rand -hex 16)"   # optional but recommended
gh secret set SMTP_USER       -R <owner>/<repo>   # paste SMTP username
gh secret set SMTP_PASS       -R <owner>/<repo>   # paste SMTP password
gh secret set MAIL_FROM       -R <owner>/<repo> -b 'backups@example.com'
gh secret set MAIL_TO         -R <owner>/<repo> -b 'you@example.com'
gh secret set RCLONE_CONF_B64 -R <owner>/<repo> < rclone.conf.b64   # see below

gh variable set SMTP_HOST     -R <owner>/<repo> -b 'smtp.gmail.com'
```

---

## Building `RCLONE_CONF_B64`

The remote **must be named `gdrive`**. Pick **one** of the options below, then
base64 it:

```bash
base64 -w0 rclone.conf > rclone.conf.b64   # macOS: base64 -i rclone.conf -o rclone.conf.b64
```

### Option A — service account + Shared Drive (recommended, no expiry)

The credential never expires and needs no browser, which is right for unattended
hourly runs. Requires Google Workspace (for Shared Drives).

1. **Google Cloud Console** → pick/create a project → **APIs & Services →
   Library** → enable **Google Drive API**.
2. **APIs & Services → Credentials → Create credentials → Service account.**
   Name it e.g. `appliku-backups`. No roles needed. Create.
3. Open the service account → **Keys → Add key → Create new key → JSON** →
   download the file (e.g. `sa.json`). Note its `client_email`
   (`appliku-backups@<project>.iam.gserviceaccount.com`).
4. **Google Drive** (drive.google.com) → **Shared drives → New** → name it e.g.
   `ApplikuBackups`.
5. Open that Shared Drive → **Manage members** → add the service account's
   `client_email` as **Content manager** (or Manager).
6. Copy the **Shared Drive ID** from the URL while it's open:
   `https://drive.google.com/drive/folders/<SHARED_DRIVE_ID>`.
7. Build and store the secret:
   ```bash
   python3 scripts/sa_to_rclone.py sa.json <SHARED_DRIVE_ID> > rclone.conf
   base64 -w0 rclone.conf | gh secret set RCLONE_CONF_B64 -R <owner>/<repo>
   ```
Files land under `<SharedDrive>/ApplikuBackups/<app>/…` (rclone auto-creates the
subfolders). Service accounts have no personal Drive quota, so a **Shared Drive
is required** — a plain folder shared from a personal Gmail will fail.

### Option B — reuse an existing OAuth token

If you already have a google-auth `token.json` (scope `drive.file`), convert it:

```bash
python3 scripts/token_to_rclone.py /path/to/token.json > rclone.conf
base64 -w0 rclone.conf > rclone.conf.b64
```

> ⚠️ **OAuth longevity:** if the Google consent screen is in **Testing** mode,
> refresh tokens expire after **7 days**. Only viable if the OAuth app is
> **Published / In production**. Prefer Option A.

### Option C — fresh interactive `rclone config`

```bash
rclone config   # New remote -> name "gdrive" -> drive -> scope 1 (drive)
                # auto config -> authorize in browser
rclone lsd gdrive:        # smoke test
```
Config lives at `rclone config file`. Same 7-day caveat as Option B unless the
consent screen is published.

---

## Rollout

1. **Push the branch** and add the secrets/variables above.
2. **Test on one app.** Actions → *Appliku DB backups* → **Run workflow**, set
   `only_app = <your-app>` (the app name, slug, or its `db-...` id all work).
   Confirm:
   - `gdrive:ApplikuBackups/<your-app>/db_<your-app>_*.sql.gz` exists and is
     valid gzip:
     `rclone copyto gdrive:ApplikuBackups/<your-app>/<file> - | gunzip -t && echo OK`
   - the summary email arrives at `MAIL_TO` (manual runs always email) and lists
     each app next to its `db-...` id;
   - after 48+ runs, prune keeps only the newest 48 (check `rclone lsjson`).
3. **Test restore (dry run):** Actions → *Appliku DB restore (manual)* →
   `app = <db-id>` (copy it from the email/Actions tab), leave `confirm` blank →
   downloads + verifies gzip, **no DB writes**.
4. **Test a real restore into a throwaway DB:** set `app = <db-id>`,
   `confirm = <db-id>`, `target_url = postgres://…throwaway…`, `wipe = true`.
   Check the smoke-test row counts in the log/email.
5. **Go fleet-wide:** the hourly `schedule` is already enabled. Leave `only_app`
   blank for scheduled runs (it only applies to manual dispatch). Watch the
   first couple of hourly runs.

---

## Restore runbook

Workflow **Appliku DB restore (manual)** — inputs:

| Input | Meaning |
| --- | --- |
| `app` | Database to restore, given as its **opaque id** (`db-...`, from the Actions tab or the email). A real slug/name also works but would show in the dispatch UI. |
| `file` | Specific dump filename; blank = newest in the folder. |
| `confirm` | Must equal the `app` id **exactly** to actually write. Otherwise **dry run**. |
| `target_url` | Restore into this Postgres URL instead of the live DB (DR/testing). |
| `wipe` | `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` before load (clean restore). |

- **Default = dry run:** download + `gunzip -t`, no DB change.
- **Live restore** (confirm matches): loads into `target_url` if given, else the
  app's live DB resolved from the Appliku API, then prints a couple of row
  counts as a smoke check. Outcome is emailed to `MAIL_TO`.

---

## Email volume control

Hourly runs = up to 24 emails/day. Default policy (`ALWAYS_EMAIL=false`):

- **Any failure → email that run** (`[Appliku Backups] ⚠ N FAILED`).
- **All‑success → one digest/day** at `DIGEST_HOUR` (`[Appliku Backups] OK N/N`).
- **Manual runs always email** (so testing shows a result).

Flip `ALWAYS_EMAIL=true` to get an email every run.

---

## Operational notes & caveats

- **Public repo + GitHub Actions:** public repositories get unlimited Actions
  minutes on GitHub-hosted runners. All credentials are supplied via **encrypted
  Actions secrets**, which are not exposed to workflows triggered from forks and
  are masked in logs.
- **pg_dump version:** must be ≥ server. We install **client 17** to cover a
  PG17 datastore; revisit if Appliku upgrades servers further.
- **Data egress:** prod dumps transit GitHub-hosted runners. If that's a
  concern, register a **self-hosted runner** (label it and set `runs-on`
  accordingly) so dumps never leave your infra.
- **Public Postgres exposure** is required and must be enabled per datastore.
  GitHub runner IPs are too broad to allowlist — strong DB credentials are the
  control.
- **DB URL handling:** `discover.py` keeps connection URLs out of the job matrix
  entirely (each leg re-resolves its own url at runtime) and every leg registers
  an `::add-mask::` before using it, so URLs never surface in logs or the run UI.
- **No client names on the public surface:** job matrix, job names, logs and
  artifacts use opaque `db-<hash>` ids only; real names live solely in private
  Drive folders and the email (see *Anonymization*). Tighten further with
  Settings → Actions → "Require approval for all outside collaborators" so fork
  PRs can't run workflows.
- **Schedule reliability:** GitHub `schedule` is best-effort (can lag minutes)
  and auto-disables after **60 days** of repo inactivity — `keepalive.yml`
  (weekly) prevents that. For tighter timing you can also POST a
  `repository_dispatch` of type `hourly-backup` from an external cron.

---

## Files

```
.github/workflows/backup.yml      hourly discover → backup → notify
.github/workflows/restore.yml     manual guarded restore
.github/workflows/keepalive.yml   weekly keep-schedule-alive ping
scripts/discover.py               enumerate DBs → matrix / --resolve <app>
scripts/backup_one.sh             dump+gzip+upload+prune one DB, write status
scripts/prune.py                  keep newest N per app folder (rclone lsjson)
scripts/restore_one.sh            guarded restore of one dump
scripts/build_summary.py          backup email body + send decision
scripts/restore_email.py          restore email body
scripts/sa_to_rclone.py           service account + Shared Drive → rclone.conf
scripts/token_to_rclone.py        OAuth token.json → rclone.conf helper
config/exclude.txt                app names to skip
```
