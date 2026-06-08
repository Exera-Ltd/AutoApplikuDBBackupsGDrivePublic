#!/usr/bin/env python3
"""Enumerate Appliku-hosted Postgres databases with external access.

Two modes:

  discover.py                 Print a GitHub Actions matrix to $GITHUB_OUTPUT
                              (and to stdout) of every externally-reachable
                              Postgres datastore: {"include":[{app, db_url}, ...]}.

  discover.py --resolve APP   Print the public connection_url for a single app
                              (used by the restore workflow). Exits non-zero if
                              the app has no external Postgres datastore.

Selection rule (per Appliku account, verified live):
  GET /api/team/<TEAM>/applications/list/        -> all applications
  GET /api/team/<TEAM>/applications/<id>/datastores
     keep datastores where kind == "database"
                       and allow_external_connections is true
     use properties.connection_url (public host:port URL)

Environment:
  APPLIKU_TOKEN   (required) Appliku API token (Authorization: Token <...>)
  APPLIKU_TEAM    (required) Appliku team slug (the <TEAM> in the API paths above)
  ONLY_APP        (optional) restrict matrix to a single app name (blank = all)

Excludes: app names listed in config/exclude.txt (one per line, '#' comments).
The db_url is registered as a GitHub Actions mask so it never prints in logs.
"""
import json
import os
import sys
import urllib.request
import urllib.error

API_ROOT = "https://api.appliku.com/api/team"
TEAM = os.environ.get("APPLIKU_TEAM", "")
TOKEN = os.environ.get("APPLIKU_TOKEN", "")


def _api(path):
    url = "%s/%s%s" % (API_ROOT, TEAM, path)
    req = urllib.request.Request(url, headers={"Authorization": "Token " + TOKEN})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.load(r)


def load_excludes():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "config", "exclude.txt")
    names = set()
    try:
        with open(path) as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if line:
                    names.add(line)
    except FileNotFoundError:
        pass
    return names


def iter_databases():
    """Yield (app_name, datastore_id, connection_url) for each external Postgres DB."""
    apps = _api("/applications/list/")
    for app in apps:
        app_id = app["id"]
        app_name = app["name"]
        try:
            datastores = _api("/applications/%s/datastores" % app_id)
        except urllib.error.HTTPError as exc:
            sys.stderr.write("WARN: datastores fetch failed for %s: %s\n" % (app_name, exc))
            continue
        for ds in datastores:
            if ds.get("kind") != "database":
                continue
            if not ds.get("allow_external_connections"):
                continue
            url = (ds.get("properties") or {}).get("connection_url")
            if url:
                yield app_name, ds.get("id"), url


def slugged_databases():
    """Like iter_databases but with a unique slug per DB.

    Single-DB apps keep the bare app name; apps with more than one external DB
    get "<app>_<datastore_id>" so folders/files/jobs never collide.
    Yields (slug, app_name, url).
    """
    rows = list(iter_databases())
    counts = {}
    for name, _ds, _url in rows:
        counts[name] = counts.get(name, 0) + 1
    for name, ds_id, url in rows:
        slug = name if counts[name] == 1 else "%s_%s" % (name, ds_id)
        yield slug, name, url


def resolve(target):
    """Print the DB url for a slug (preferred) or bare app name, nothing else.

    Stdout is exactly the url so callers can capture it with $(...). The caller
    is responsible for registering an `::add-mask::` before using it.
    """
    for slug, name, url in slugged_databases():
        if target in (slug, name):
            print(url)
            return 0
    sys.stderr.write("ERROR: no external Postgres datastore found for '%s'\n" % target)
    return 1


def build_matrix():
    only = os.environ.get("ONLY_APP", "").strip()
    excludes = load_excludes()
    include = []
    for slug, name, _url in slugged_databases():
        # `only` may be given as either the app name or a specific slug.
        if only and only not in (slug, name):
            continue
        if name in excludes or slug in excludes:
            sys.stderr.write("skip (excluded): %s\n" % slug)
            continue
        # IMPORTANT: the matrix must NOT contain the db_url. A masked value in a
        # job output makes GitHub silently drop the whole output ("Skip output
        # ... may contain secret"). Each backup leg re-resolves its own url.
        include.append({"app": slug})

    include.sort(key=lambda d: d["app"])
    matrix = {"include": include}
    payload = json.dumps(matrix, separators=(",", ":"))

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write("matrix=%s\n" % payload)
            fh.write("count=%d\n" % len(include))
    # Human-readable echo (app names only; urls are masked).
    sys.stderr.write("discovered %d database(s): %s\n" % (
        len(include), ", ".join(d["app"] for d in include)))
    print(payload)
    return 0 if include else 1


def main(argv):
    if not TOKEN:
        sys.stderr.write("ERROR: APPLIKU_TOKEN is not set\n")
        return 2
    if not TEAM:
        sys.stderr.write("ERROR: APPLIKU_TEAM is not set\n")
        return 2
    if len(argv) >= 2 and argv[1] == "--resolve":
        if len(argv) < 3:
            sys.stderr.write("usage: discover.py --resolve <app>\n")
            return 2
        return resolve(argv[2])
    return build_matrix()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
