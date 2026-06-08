#!/usr/bin/env bash
# Guarded restore of one database's dump from Google Drive into Postgres.
#
#   restore_one.sh
#
# Runs in a PUBLIC repo, so the restore log must not reveal the real app name:
# the database is addressed by its opaque id, and the real slug (resolved for
# the Drive folder) is masked.
#
# Driven entirely by environment variables (set by restore.yml):
#   APP         (required) the database to restore, given as its opaque id
#               (db-...., from the Actions tab / email). A real slug or app
#               name also works but would appear in the public dispatch UI.
#   FILE        backup filename to restore; blank = newest in the folder
#   CONFIRM     must equal APP to perform a real write; otherwise DRY RUN
#   TARGET_URL  restore into this DB instead of the live DB (DR / testing)
#   WIPE        "true" => DROP SCHEMA public CASCADE; CREATE SCHEMA public; first
#   APPLIKU_TOKEN / APPLIKU_TEAM / ANON_SALT   to resolve id -> slug/url
#   RCLONE_REMOTE (default gdrive)  RCLONE_BASE (default ApplikuBackups)
#
# Safety model:
#   - If CONFIRM != APP, this is a DRY RUN: download + `gunzip -t` only, no DB
#     writes whatsoever.
#   - Live DB writes happen only when CONFIRM == APP.
#   - Writes go to TARGET_URL if provided, else the database's live
#     connection_url resolved live from the Appliku API.
#
# Writes status_restore.json for the email summary. Never prints any db_url or
# real app name to stdout.
set -u -o pipefail

TARGET="${APP:?APP is required (the opaque db-... id)}"
FILE="${FILE:-}"
CONFIRM="${CONFIRM:-}"
TARGET_URL="${TARGET_URL:-}"
WIPE="${WIPE:-false}"
REMOTE="${RCLONE_REMOTE:-gdrive}"
BASE="${RCLONE_BASE:-ApplikuBackups}"
STATUS="status_restore.json"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECONDS=0

write_status() { # ok message mode rows
  python3 - "$TARGET" "$SLUG" "$1" "$2" "$3" "${4:-}" "$SECONDS" "${FILE:-}" >"$STATUS" <<'PY'
import json, sys
oid, app, ok, msg, mode, rows, dur, fn = sys.argv[1:9]
json.dump({"id": oid, "app": app, "ok": ok == "true", "mode": mode,
           "message": msg, "rows": rows, "duration": int(dur), "file": fn},
          open(1, "w"))
PY
}
die() { echo "✗ restore ${TARGET}: $1" >&2; write_status false "$1" "${MODE:-error}" ""; exit 1; }

# ---- resolve the real slug (private) from the opaque id -------------------
# Masked immediately so neither the slug nor anything derived from it (the
# Drive folder path, the dump filename) can leak into this public log.
SLUG="$(python3 "${here}/discover.py" --name "$TARGET")" || { SLUG=""; die "unknown database '${TARGET}'"; }
echo "::add-mask::${SLUG}"
FOLDER="${REMOTE}:${BASE}/${SLUG}"

# ---- resolve target file -------------------------------------------------
if [ -z "${FILE}" ]; then
  echo "→ ${TARGET}: resolving newest dump"
  FILE="$(rclone lsjson "${FOLDER}" 2>/dev/null \
    | python3 -c 'import json,sys; xs=[e["Name"] for e in json.load(sys.stdin) if not e.get("IsDir") and e["Name"].endswith(".sql.gz")]; print(sorted(xs)[-1] if xs else "")')"
  [ -n "${FILE}" ] || die "no .sql.gz dumps found for ${TARGET}"
fi
echo "  file: ${FILE}"   # real slug inside the name is masked

# ---- download + integrity check (always) ---------------------------------
echo "→ ${TARGET}: downloading dump"
rclone copyto "${FOLDER}/${FILE}" "./${FILE}" || die "download failed"
gunzip -t "./${FILE}" || die "gzip integrity check failed"
echo "  gzip OK"

# ---- decide dry run vs live ----------------------------------------------
if [ "${CONFIRM}" != "${TARGET}" ]; then
  MODE="dryrun"
  echo "✓ DRY RUN: confirm != id ('${TARGET}'); no DB changes made."
  write_status true "Dry run: downloaded and verified ${FILE}; no DB writes." dryrun ""
  rm -f "./${FILE}"
  exit 0
fi
MODE="restore"

# ---- resolve destination URL ---------------------------------------------
if [ -n "${TARGET_URL}" ]; then
  URL="${TARGET_URL}"
  echo "→ restoring into provided TARGET_URL"
else
  echo "→ ${TARGET}: resolving live DB url from Appliku API"
  URL="$(python3 "${here}/discover.py" --resolve "${TARGET}")" || die "could not resolve live DB url"
  echo "→ ${TARGET}: restoring into the LIVE database"
fi
echo "::add-mask::${URL}"

# ---- optional wipe -------------------------------------------------------
if [ "${WIPE}" = "true" ]; then
  echo "→ wiping target schema (DROP SCHEMA public CASCADE; CREATE SCHEMA public;)"
  psql "${URL}" -v ON_ERROR_STOP=1 \
    -c 'DROP SCHEMA public CASCADE;' -c 'CREATE SCHEMA public;' \
    || die "schema wipe failed"
fi

# ---- restore -------------------------------------------------------------
echo "→ loading dump"
if ! gunzip -c "./${FILE}" | psql "${URL}" -v ON_ERROR_STOP=1 >/tmp/restore.log 2>&1; then
  tail -20 /tmp/restore.log >&2 || true
  die "psql restore failed (see log)"
fi

# ---- smoke check: a couple of row counts ---------------------------------
ROWS="$(psql "${URL}" -tA <<'SQL' 2>/dev/null | tr '\n' ' '
SELECT 'tables=' || count(*) FROM information_schema.tables WHERE table_schema='public';
SELECT 'auth_user=' || count(*) FROM auth_user;
SQL
)" || ROWS="(smoke check could not run)"
echo "  smoke: ${ROWS}"

rm -f "./${FILE}"
echo "✓ restore ${TARGET} complete in ${SECONDS}s"
write_status true "Restored ${FILE}." restore "${ROWS}"
