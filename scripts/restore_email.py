#!/usr/bin/env python3
"""Build the restore-outcome email body + subject from status_restore.json.

Writes body.html and emits subject= to $GITHUB_OUTPUT.
Env: APP (fallback name), RUN_URL.
"""
import json
import os

try:
    s = json.load(open("status_restore.json"))
except Exception:
    s = {"app": os.environ.get("APP", "?"), "ok": False, "mode": "error",
         "message": "restore step produced no status file", "rows": "", "file": ""}

ok = bool(s.get("ok"))
mode = s.get("mode", "")
subject = "[Appliku Restore] %s %s%s" % (
    "OK" if ok else "FAILED",
    s.get("app", "?"),
    " (dry run)" if mode == "dryrun" else "")
color = "#1a7f37" if ok else "#cf222e"

html = """<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:14px">
  <h2>Appliku restore: {app}</h2>
  <p style="color:{color};font-weight:600">{status} &middot; mode: {mode}</p>
  <table style="border-collapse:collapse">
    <tr><td style="padding:3px 8px">File</td><td style="padding:3px 8px">{file}</td></tr>
    <tr><td style="padding:3px 8px">Message</td><td style="padding:3px 8px">{msg}</td></tr>
    <tr><td style="padding:3px 8px">Smoke check</td><td style="padding:3px 8px">{rows}</td></tr>
  </table>
  <p><a href="{url}">View run &rarr;</a></p>
</div>""".format(
    app=s.get("app", "?"), color=color,
    status="OK" if ok else "FAILED", mode=mode,
    file=s.get("file", "") or "(none)", msg=s.get("message", ""),
    rows=s.get("rows", "") or "—", url=os.environ.get("RUN_URL", "#"))

open("body.html", "w").write(html)
out = os.environ.get("GITHUB_OUTPUT")
if out:
    with open(out, "a") as f:
        f.write("subject=%s\n" % subject)
print(subject)
