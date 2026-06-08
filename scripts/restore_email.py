#!/usr/bin/env python3
"""Build the restore-outcome email body + subject from status_restore.json.

The email (a private channel) shows the real app name; stdout (this runs in a
PUBLIC repo) shows only the opaque id, so the real name never reaches the log.

Writes body.html and emits subject= to $GITHUB_OUTPUT (step outputs are not
printed to the log). Env: APP (fallback id), RUN_URL.
"""
import html
import json
import os

try:
    s = json.load(open("status_restore.json"))
except Exception:
    s = {"id": os.environ.get("APP", "?"), "app": os.environ.get("APP", "?"),
         "ok": False, "mode": "error",
         "message": "restore step produced no status file", "rows": "", "file": ""}

ok = bool(s.get("ok"))
mode = s.get("mode", "")
oid = s.get("id", "?")
app = s.get("app", oid)  # real name for the (private) email

# Email subject: real name is fine here (the email is private).
subject = "[Appliku Restore] %s %s%s" % (
    "OK" if ok else "FAILED", app,
    " (dry run)" if mode == "dryrun" else "")
color = "#1a7f37" if ok else "#cf222e"

html_body = """<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:14px">
  <h2>Appliku restore: {app}</h2>
  <p style="color:{color};font-weight:600">{status} &middot; mode: {mode}</p>
  <table style="border-collapse:collapse">
    <tr><td style="padding:3px 8px">ID</td><td style="padding:3px 8px"><code>{oid}</code></td></tr>
    <tr><td style="padding:3px 8px">File</td><td style="padding:3px 8px">{file}</td></tr>
    <tr><td style="padding:3px 8px">Message</td><td style="padding:3px 8px">{msg}</td></tr>
    <tr><td style="padding:3px 8px">Smoke check</td><td style="padding:3px 8px">{rows}</td></tr>
  </table>
  <p><a href="{url}">View run &rarr;</a></p>
</div>""".format(
    app=html.escape(str(app)), color=color,
    status="OK" if ok else "FAILED", mode=html.escape(str(mode)),
    oid=html.escape(str(oid)),
    file=html.escape(str(s.get("file", "") or "(none)")),
    msg=html.escape(str(s.get("message", ""))),
    rows=html.escape(str(s.get("rows", "") or "—")),
    url=os.environ.get("RUN_URL", "#"))

open("body.html", "w").write(html_body)
out = os.environ.get("GITHUB_OUTPUT")
if out:
    with open(out, "a") as f:
        f.write("subject=%s\n" % subject)
# Public log: opaque id only — never the real app name.
print("[Appliku Restore] %s %s%s" % (
    "OK" if ok else "FAILED", oid,
    " (dry run)" if mode == "dryrun" else ""))
