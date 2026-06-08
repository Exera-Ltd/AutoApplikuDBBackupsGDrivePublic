#!/usr/bin/env python3
"""Assemble the backup-run email summary from per-app status JSON files.

Reads every status_*.json under a directory (downloaded backup artifacts),
writes an HTML body to --out, and emits to $GITHUB_OUTPUT:
  subject=...        the email subject line
  send=true|false    whether the notify step should actually send mail
  failed=N           number of failed apps

Send policy (matches the agreed volume control):
  * Any failure                        -> always send (subject "⚠ N FAILED").
  * Manual run (workflow_dispatch)      -> always send.
  * All-success scheduled run           -> send only if ALWAYS_EMAIL=true OR the
                                           run's UTC hour == DIGEST_HOUR (one
                                           daily digest, default 06:00Z).

Env:
  ALWAYS_EMAIL  "true" to email on every run (default false)
  DIGEST_HOUR   UTC hour for the daily all-success digest (default 6)
  EVENT_NAME    GitHub event name (schedule / workflow_dispatch)
  RUN_URL       link to the Actions run
  RUN_STARTED   ISO timestamp of the run start (optional, for display)
"""
import glob
import json
import os
import sys
from datetime import datetime, timezone

STATUS_DIR = sys.argv[1] if len(sys.argv) > 1 else "."
OUT = "body.html"
for i, a in enumerate(sys.argv):
    if a == "--out" and i + 1 < len(sys.argv):
        OUT = sys.argv[i + 1]


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%.0f %s" if unit == "B" else "%.1f %s") % (n, unit)
        n /= 1024


def load():
    rows = []
    for path in sorted(glob.glob(os.path.join(STATUS_DIR, "**", "status_*.json"),
                                 recursive=True)):
        try:
            rows.append(json.load(open(path)))
        except (ValueError, OSError):
            continue
    # De-dup by app (one artifact per app), keep last seen.
    by_app = {}
    for r in rows:
        by_app[r.get("app", "?")] = r
    return [by_app[k] for k in sorted(by_app)]


def main():
    rows = load()
    total = len(rows)
    failed = [r for r in rows if not r.get("ok")]
    ok = total - len(failed)
    total_bytes = sum(int(r.get("bytes", 0) or 0) for r in rows)

    if total == 0:
        subject = "[Appliku Backups] ⚠ no backups ran"
    elif failed:
        subject = "[Appliku Backups] ⚠ %d FAILED" % len(failed)
    else:
        subject = "[Appliku Backups] OK %d/%d" % (ok, total)

    run_url = os.environ.get("RUN_URL", "")
    started = os.environ.get("RUN_STARTED") or datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%SZ")

    tr = []
    for r in rows:
        good = r.get("ok")
        color = "#1a7f37" if good else "#cf222e"
        icon = "✓" if good else "✗"
        size = human(r.get("bytes", 0)) if good else "—"
        detail = "" if good else (r.get("error") or "failed")
        tr.append(
            "<tr>"
            "<td style='padding:4px 10px'>%s</td>"
            "<td style='padding:4px 10px;color:%s;font-weight:600'>%s %s</td>"
            "<td style='padding:4px 10px;text-align:right'>%s</td>"
            "<td style='padding:4px 10px;text-align:right'>%ss</td>"
            "<td style='padding:4px 10px;color:#cf222e'>%s</td>"
            "</tr>" % (r.get("app", "?"), color, icon,
                      "OK" if good else "FAIL", size,
                      r.get("duration", "?"), detail))

    html = """\
<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:14px;color:#1f2328">
  <h2 style="margin:0 0 4px">Appliku &rarr; Google Drive backup</h2>
  <p style="margin:0 0 12px;color:#57606a">Run started {started} &middot; {ok}/{total} OK &middot; {failedn} failed &middot; total {tot}</p>
  <table style="border-collapse:collapse;border:1px solid #d0d7de;min-width:520px">
    <thead><tr style="background:#f6f8fa">
      <th style="padding:6px 10px;text-align:left">App</th>
      <th style="padding:6px 10px;text-align:left">Status</th>
      <th style="padding:6px 10px;text-align:right">Size</th>
      <th style="padding:6px 10px;text-align:right">Time</th>
      <th style="padding:6px 10px;text-align:left">Error</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <p style="margin:14px 0 0"><a href="{url}">View the workflow run &rarr;</a></p>
</div>""".format(started=started, ok=ok, total=total, failedn=len(failed),
                 tot=human(total_bytes), rows="".join(tr) or
                 "<tr><td colspan=5 style='padding:8px;color:#cf222e'>"
                 "No status files found — discovery or all backup legs failed.</td></tr>",
                 url=run_url or "#")

    with open(OUT, "w") as fh:
        fh.write(html)

    # ---- send decision ----
    event = os.environ.get("EVENT_NAME", "")
    always = os.environ.get("ALWAYS_EMAIL", "false").lower() == "true"
    digest_hour = int(os.environ.get("DIGEST_HOUR", "6"))
    hour = datetime.now(timezone.utc).hour
    if failed or total == 0:
        send = True            # failures (or total discovery failure) always notify
    elif event == "workflow_dispatch":
        send = True            # manual runs always show a result
    elif always:
        send = True
    else:
        send = (hour == digest_hour)

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write("subject=%s\n" % subject)
            fh.write("send=%s\n" % ("true" if send else "false"))
            fh.write("failed=%d\n" % len(failed))
    print("subject: %s" % subject)
    print("send: %s (event=%s always=%s hour=%s digest=%s failed=%d)"
          % (send, event, always, hour, digest_hour, len(failed)))


if __name__ == "__main__":
    main()
