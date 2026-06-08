#!/usr/bin/env python3
"""Keep only the newest N dumps in an app's Google Drive backup folder.

Usage:
  prune.py <app> [keep]

Lists gdrive:ApplikuBackups/<app> via `rclone lsjson`, keeps the newest <keep>
*.sql.gz files (by filename, which embeds a sortable UTC timestamp, falling back
to ModTime), and deletes the rest with `rclone deletefile`.

Env:
  RCLONE_REMOTE   remote name, default "gdrive"
  RCLONE_BASE     base folder, default "ApplikuBackups"
  KEEP            default retention if [keep] arg omitted, default 48
"""
import json
import os
import subprocess
import sys

REMOTE = os.environ.get("RCLONE_REMOTE", "gdrive")
BASE = os.environ.get("RCLONE_BASE", "ApplikuBackups")


def main(argv):
    if len(argv) < 2:
        sys.stderr.write("usage: prune.py <app> [keep]\n")
        return 2
    app = argv[1]
    keep = int(argv[2]) if len(argv) > 2 else int(os.environ.get("KEEP", "48"))
    folder = "%s:%s/%s" % (REMOTE, BASE, app)

    try:
        raw = subprocess.check_output(
            ["rclone", "lsjson", folder], stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as exc:
        # A missing folder (nothing uploaded yet) is not an error.
        msg = exc.output.decode("utf-8", "replace")
        if "directory not found" in msg.lower():
            print("prune: folder %s does not exist yet, nothing to do" % folder)
            return 0
        sys.stderr.write(msg)
        return exc.returncode

    items = [e for e in json.loads(raw or "[]")
             if not e.get("IsDir") and e.get("Name", "").endswith(".sql.gz")]
    # Newest first: filename embeds YYYY-MM-DD_HHMMZ so lexical sort == chronological.
    items.sort(key=lambda e: (e.get("Name", ""), e.get("ModTime", "")), reverse=True)

    survivors = items[:keep]
    doomed = items[keep:]
    print("prune %s: %d file(s), keeping %d, deleting %d"
          % (app, len(items), len(survivors), len(doomed)))

    failures = 0
    for e in doomed:
        target = "%s/%s" % (folder, e["Name"])
        try:
            subprocess.check_call(["rclone", "deletefile", target])
            print("  deleted %s" % e["Name"])
        except subprocess.CalledProcessError:
            sys.stderr.write("  WARN: failed to delete %s\n" % e["Name"])
            failures += 1
    # Pruning is best-effort; never fail the backup leg over a stale file.
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
