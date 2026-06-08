#!/usr/bin/env bash
# Dump one Postgres database, gzip it, upload to Google Drive, prune old dumps.
#
#   backup_one.sh <app> <db_url>
#
# Writes a status JSON file (status_<app>.json) describing the outcome so the
# notify job can assemble the email summary. Never prints the db_url.
#
# Env:
#   RCLONE_REMOTE  default "gdrive"
#   RCLONE_BASE    default "ApplikuBackups"
#   KEEP           retention per app, default 48
#   MIN_BYTES      reject dumps smaller than this (sanity check), default 100
set -u -o pipefail

APP="${1:?usage: backup_one.sh <app> <db_url>}"
DB_URL="${2:?usage: backup_one.sh <app> <db_url>}"
echo "::add-mask::${DB_URL}"

REMOTE="${RCLONE_REMOTE:-gdrive}"
BASE="${RCLONE_BASE:-ApplikuBackups}"
KEEP="${KEEP:-48}"
MIN_BYTES="${MIN_BYTES:-100}"

STAMP="$(date -u +%Y-%m-%d_%H%MZ)"
FILE="db_${APP}_${STAMP}.sql.gz"
DEST="${REMOTE}:${BASE}/${APP}/${FILE}"
STATUS="status_${APP}.json"
SECONDS=0

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

fail() {
  local msg="$1"
  echo "✗ ${APP}: ${msg}" >&2
  python3 - "$APP" "$msg" "$SECONDS" "$FILE" >"$STATUS" <<'PY'
import json, sys
app, err, dur, fn = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
json.dump({"app": app, "ok": False, "bytes": 0, "file": fn,
           "duration": dur, "error": err}, open(1, "w"))
PY
  exit 1
}

echo "→ ${APP}: dumping to ${FILE}"
# pipefail makes a pg_dump failure abort the pipeline even though gzip succeeds.
if ! pg_dump --no-owner --no-privileges "${DB_URL}" | gzip -9 > "${FILE}"; then
  fail "pg_dump failed"
fi

BYTES="$(stat -c%s "${FILE}" 2>/dev/null || echo 0)"
echo "  size: ${BYTES} bytes"
[ "${BYTES}" -ge "${MIN_BYTES}" ] || fail "dump too small (${BYTES} bytes)"
# Integrity: the gzip must be readable end to end.
gzip -t "${FILE}" || fail "gzip integrity check failed"

echo "  uploading to ${DEST}"
rclone copyto "${FILE}" "${DEST}" --drive-use-trash=false || fail "rclone upload failed"

# Prune is best-effort and must not flip a good backup to failed.
python3 "${here}/prune.py" "${APP}" "${KEEP}" || echo "  WARN: prune reported an issue" >&2

rm -f "${FILE}"

echo "✓ ${APP}: ${BYTES} bytes in ${SECONDS}s"
python3 - "$APP" "$BYTES" "$SECONDS" "$FILE" >"$STATUS" <<'PY'
import json, sys
app, b, dur, fn = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
json.dump({"app": app, "ok": True, "bytes": b, "file": fn,
           "duration": dur, "error": ""}, open(1, "w"))
PY
