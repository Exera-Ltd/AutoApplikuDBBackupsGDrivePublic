#!/usr/bin/env bash
# Guarded restore of one app's dump from Google Drive into a Postgres database.
#
#   restore_one.sh
#
# Driven entirely by environment variables (set by restore.yml):
#   APP         (required) app name (the ApplikuBackups/<app> folder name)
#   FILE        backup filename to restore; blank = newest in the app's folder
#   CONFIRM     must equal APP to perform a real write; otherwise DRY RUN
#   TARGET_URL  restore into this DB instead of the app's live DB (DR / testing)
#   WIPE        "true" => DROP SCHEMA public CASCADE; CREATE SCHEMA public; first
#   RCLONE_REMOTE (default gdrive)  RCLONE_BASE (default ApplikuBackups)
#
# Safety model:
#   - If CONFIRM != APP, this is a DRY RUN: download + `gunzip -t` only, no DB
#     writes whatsoever.
#   - Live DB writes happen only when CONFIRM == APP.
#   - Writes go to TARGET_URL if provided, else the app's live connection_url
#     resolved live from the Appliku API.
#
# Writes status_restore.json for the email summary. Never prints any db_url.
set -u -o pipefail

APP="${APP:?APP is required}"
FILE="${FILE:-}"
CONFIRM="${CONFIRM:-}"
TARGET_URL="${TARGET_URL:-}"
WIPE="${WIPE:-false}"
REMOTE="${RCLONE_REMOTE:-gdrive}"
BASE="${RCLONE_BASE:-ApplikuBackups}"
FOLDER="${REMOTE}:${BASE}/${APP}"
STATUS="status_restore.json"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECONDS=0

write_status() { # ok message mode rows
  python3 - "$APP" "$1" "$2" "$3" "${4:-}" "$SECONDS" "${FILE:-}" >"$STATUS" <<'PY'
import json, sys
app, ok, msg, mode, rows, dur, fn = sys.argv[1:8]
json.dump({"app": app, "ok": ok == "true", "mode": mode, "message": msg,
           "rows": rows, "duration": int(dur), "file": fn}, open(1, "w"))
PY
}
die() { echo "✗ restore ${APP}: $1" >&2; write_status false "$1" "${MODE:-error}" ""; exit 1; }

# ---- resolve target file -------------------------------------------------
if [ -z "${FILE}" ]; then
  echo "→ resolving newest dump in ${FOLDER}"
  FILE="$(rclone lsjson "${FOLDER}" 2>/dev/null \
    | python3 -c 'import json,sys; xs=[e["Name"] for e in json.load(sys.stdin) if not e.get("IsDir") and e["Name"].endswith(".sql.gz")]; print(sorted(xs)[-1] if xs else "")')"
  [ -n "${FILE}" ] || die "no .sql.gz dumps found in ${FOLDER}"
fi
echo "  file: ${FILE}"

# ---- download + integrity check (always) ---------------------------------
echo "→ downloading ${FOLDER}/${FILE}"
rclone copyto "${FOLDER}/${FILE}" "./${FILE}" || die "download failed"
gunzip -t "./${FILE}" || die "gzip integrity check failed"
echo "  gzip OK"

# ---- decide dry run vs live ----------------------------------------------
if [ "${CONFIRM}" != "${APP}" ]; then
  MODE="dryrun"
  echo "✓ DRY RUN: confirm ('${CONFIRM}') != app ('${APP}'); no DB changes made."
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
  echo "→ resolving live DB url for ${APP} from Appliku API"
  URL="$(python3 "${here}/discover.py" --resolve "${APP}")" || die "could not resolve live DB url"
  echo "→ restoring into ${APP}'s LIVE database"
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
echo "✓ restore ${APP} complete in ${SECONDS}s"
write_status true "Restored ${FILE} into ${APP}." restore "${ROWS}"
