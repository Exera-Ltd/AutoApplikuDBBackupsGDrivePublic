#!/usr/bin/env bash
# Dump one Postgres database, gzip it, upload to Google Drive, prune old dumps.
#
#   backup_one.sh <id> <app> <db_url>
#
#   <id>      opaque public identifier (db-....). Used for ALL human-visible
#             output and for the status filename, because this runs in a PUBLIC
#             repo whose logs/artifacts are world-readable.
#   <app>     real app slug. Used ONLY for the (private) Google Drive folder and
#             the dump filename. Masked on entry so it can never leak to logs.
#   <db_url>  Postgres connection URL. Masked on entry; never printed.
#
# Writes a status JSON file (status_<id>.json) keyed by the opaque id and
# containing NO real name, so the artifact is safe to expose publicly. The
# notify job maps the id back to the real name when building the email.
#
# Env:
#   RCLONE_REMOTE  default "gdrive"
#   RCLONE_BASE    default "ApplikuBackups"
#   KEEP           retention per app, default 48
#   MIN_BYTES      reject dumps smaller than this (sanity check), default 100
set -u -o pipefail

ID="${1:?usage: backup_one.sh <id> <app> <db_url>}"
APP="${2:?usage: backup_one.sh <id> <app> <db_url>}"
DB_URL="${3:?usage: backup_one.sh <id> <app> <db_url>}"
# Defence in depth: redact the real app name and the url from this public log.
echo "::add-mask::${APP}"
echo "::add-mask::${DB_URL}"

REMOTE="${RCLONE_REMOTE:-gdrive}"
BASE="${RCLONE_BASE:-ApplikuBackups}"
KEEP="${KEEP:-48}"
MIN_BYTES="${MIN_BYTES:-100}"

# Timestamps are in Mauritius time (UTC+4, no DST) so the human-readable names
# match local time and aren't confused at restore. The "MUT" suffix makes the
# zone explicit. Selection of "newest" for prune/restore uses the file's actual
# ModTime, so the local-time label is purely cosmetic and never misleads.
STAMP="$(TZ='Indian/Mauritius' date +%Y-%m-%d_%H%M)MUT"
DATE="$(TZ='Indian/Mauritius' date +%Y-%m-%d)"   # per-day subfolder (Mauritius)
FILE="db_${APP}_${STAMP}.sql.gz"          # real name -> private Drive only
DEST="${REMOTE}:${BASE}/${APP}/${DATE}/${FILE}"
STATUS="status_${ID}.json"                # public artifact -> opaque id only
SECONDS=0

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

write_status() { # ok bytes error
  python3 - "$ID" "$1" "$2" "$SECONDS" "$3" >"$STATUS" <<'PY'
import json, sys
oid, ok, b, dur, err = sys.argv[1], sys.argv[2] == "true", int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
json.dump({"id": oid, "ok": ok, "bytes": b, "duration": dur, "error": err}, open(1, "w"))
PY
}

fail() {
  local msg="$1"
  echo "✗ ${ID}: ${msg}" >&2
  write_status false 0 "$msg"
  exit 1
}

echo "→ ${ID}: dumping"
# pipefail makes a pg_dump failure abort the pipeline even though gzip succeeds.
if ! pg_dump --no-owner --no-privileges "${DB_URL}" | gzip -9 > "${FILE}"; then
  fail "pg_dump failed"
fi

BYTES="$(stat -c%s "${FILE}" 2>/dev/null || echo 0)"
echo "  size: ${BYTES} bytes"
[ "${BYTES}" -ge "${MIN_BYTES}" ] || fail "dump too small (${BYTES} bytes)"
# Integrity: the gzip must be readable end to end.
gzip -t "${FILE}" || fail "gzip integrity check failed"

echo "  uploading dump"
rclone copyto "${FILE}" "${DEST}" --drive-use-trash=false || fail "rclone upload failed"

# Prune is best-effort and must not flip a good backup to failed.
python3 "${here}/prune.py" "${APP}" "${KEEP}" || echo "  WARN: prune reported an issue" >&2

rm -f "${FILE}"

echo "✓ ${ID}: ${BYTES} bytes in ${SECONDS}s"
write_status true "$BYTES" ""
