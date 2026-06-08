#!/usr/bin/env python3
"""Convert an existing google-auth token.json into an rclone drive remote config.

Use this to reuse an OAuth account already authorized elsewhere (a
google-auth token.json) instead of running an interactive `rclone config`.

Usage:
  python3 scripts/token_to_rclone.py /path/to/token.json > rclone.conf
  base64 -w0 rclone.conf            # the value to store as secret RCLONE_CONF_B64

The input token.json must contain: client_id, client_secret, refresh_token
(google-auth's standard authorized-user format). rclone refreshes the access
token itself, so an expired "token"/"expiry" is fine.

NOTE on scope: if the source token was minted with scope
'https://www.googleapis.com/auth/drive.file', rclone can only see/manage files
this OAuth client creates — which is exactly what we need for an isolated
ApplikuBackups tree, but it cannot browse the rest of the Drive. See README.
"""
import json
import sys

REMOTE = "gdrive"


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: token_to_rclone.py <token.json>\n")
        return 2
    d = json.load(open(sys.argv[1]))
    for k in ("client_id", "client_secret", "refresh_token"):
        if not d.get(k):
            sys.stderr.write("ERROR: token.json missing '%s'\n" % k)
            return 1

    # rclone stores the OAuth token as a compact JSON blob. access_token may be
    # empty/expired; rclone will refresh using refresh_token + client creds.
    token = {
        "access_token": d.get("token", ""),
        "token_type": "Bearer",
        "refresh_token": d["refresh_token"],
        "expiry": d.get("expiry", "1970-01-01T00:00:00Z"),
    }
    conf = (
        "[%s]\n"
        "type = drive\n"
        "scope = drive.file\n"
        "client_id = %s\n"
        "client_secret = %s\n"
        "token = %s\n"
    ) % (REMOTE, d["client_id"], d["client_secret"],
         json.dumps(token, separators=(",", ":")))
    sys.stdout.write(conf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
