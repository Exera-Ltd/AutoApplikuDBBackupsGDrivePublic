#!/usr/bin/env python3
"""Build an rclone 'gdrive' remote backed by a service account + Shared Drive.

Produces a self-contained rclone.conf (service-account credentials embedded
inline, so the single base64'd secret needs no extra files) that uploads into a
Google Shared Drive. This credential does NOT expire, unlike OAuth refresh
tokens.

Usage:
  python3 scripts/sa_to_rclone.py <service_account.json> <shared_drive_id> > rclone.conf
  base64 -w0 rclone.conf            # -> store as GitHub secret RCLONE_CONF_B64

Find <shared_drive_id>: open the Shared Drive in a browser; the URL is
https://drive.google.com/drive/folders/<THIS_IS_THE_ID>

The service account email (client_email in the JSON) must be added as a member
of that Shared Drive with at least "Content manager" access.
"""
import json
import sys

REMOTE = "gdrive"


def main():
    if len(sys.argv) < 3:
        sys.stderr.write("usage: sa_to_rclone.py <service_account.json> <shared_drive_id>\n")
        return 2
    sa_path, drive_id = sys.argv[1], sys.argv[2]
    sa = json.load(open(sa_path))
    for k in ("client_email", "private_key", "token_uri"):
        if not sa.get(k):
            sys.stderr.write("ERROR: %s is missing '%s' (not a service-account key?)\n"
                             % (sa_path, k))
            return 1

    # rclone reads service_account_credentials as a single-line JSON string.
    creds = json.dumps(sa, separators=(",", ":"))
    conf = (
        "[%s]\n"
        "type = drive\n"
        "scope = drive\n"
        "service_account_credentials = %s\n"
        "team_drive = %s\n"
    ) % (REMOTE, creds, drive_id)
    sys.stdout.write(conf)
    sys.stderr.write("OK: remote '%s' -> Shared Drive %s as %s\n"
                     % (REMOTE, drive_id, sa["client_email"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
