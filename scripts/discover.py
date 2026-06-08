#!/usr/bin/env python3
"""Enumerate Appliku-hosted Postgres databases with external access.

Because this runs in a PUBLIC repository, the GitHub-visible surface (job
matrix, job names, logs, artifacts) must never reveal real application names.
Every database is therefore addressed publicly by an opaque, stable id
(``db-<hash>``); the real app name is used only in private channels (the Google
Drive folder and the summary email).

Modes:

  discover.py                 Print a GitHub Actions matrix to $GITHUB_OUTPUT
                              (and to stdout) of opaque ids only:
                              {"include":[{"id":"db-...."}, ...]}.

  discover.py --leg ID        Print TWO lines for one database: the real app
                              slug, then its connection_url. Used by the backup
                              leg, which masks BOTH before use. Nothing is
                              printed if the id is unknown (exit non-zero).

  discover.py --resolve TGT   Print only the connection_url for TGT (an id, a
                              slug, or a bare app name). Used by restore.

  discover.py --name TGT      Print only the real app slug for TGT (id/slug/
                              name). Used by restore to locate the Drive folder.
                              The caller masks it.

Selection rule:
  GET /api/team/<TEAM>/applications/list/        -> all applications
  GET /api/team/<TEAM>/applications/<id>/datastores
     keep datastores where kind == "database"
                       and allow_external_connections is true
     use properties.connection_url (public host:port URL)

Environment:
  APPLIKU_TOKEN   (required) Appliku API token (Authorization: Token <...>)
  APPLIKU_TEAM    (required) Appliku team slug (the <TEAM> in the API paths above)
  ANON_SALT       (optional) salt for the opaque id hash; defaults to
                  APPLIKU_TOKEN. Set a dedicated value to keep ids stable
                  across API-token rotations.
  ONLY_APP        (optional) restrict the matrix to one database, given as its
                  opaque id, slug, or app name (blank = all).

Excludes: app names/slugs listed in config/exclude.txt (one per line).
Real app names and db_urls never reach stdout except via --leg/--resolve/--name,
whose callers immediately register a GitHub `::add-mask::` for the value.
"""
import hashlib
import json
import os
import sys
import urllib.request
import urllib.error

API_ROOT = "https://api.appliku.com/api/team"
TEAM = os.environ.get("APPLIKU_TEAM", "")
TOKEN = os.environ.get("APPLIKU_TOKEN", "")
SALT = os.environ.get("ANON_SALT") or TOKEN


def anon_id(slug):
    """Opaque, stable public identifier for a database slug.

    A salted SHA-256 keeps real names out of the public GitHub surface and
    prevents an outsider from reversing the (guessable) app names by brute
    force, as long as ANON_SALT (or the API token) stays secret.
    """
    digest = hashlib.sha256(("%s|%s" % (SALT, slug)).encode("utf-8")).hexdigest()
    return "db-" + digest[:12]


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
            # Don't leak the app name on a public runner; log by anonymized id.
            sys.stderr.write("WARN: datastores fetch failed for %s: %s\n"
                             % (anon_id(app_name), exc))
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
    get "<app>_<datastore_id>" so folders/files never collide.
    Yields (slug, app_name, url).
    """
    rows = list(iter_databases())
    counts = {}
    for name, _ds, _url in rows:
        counts[name] = counts.get(name, 0) + 1
    for name, ds_id, url in rows:
        slug = name if counts[name] == 1 else "%s_%s" % (name, ds_id)
        yield slug, name, url


def _find(target):
    """Return (slug, name, url) for a target given as opaque id, slug, or name."""
    for slug, name, url in slugged_databases():
        if target in (anon_id(slug), slug, name):
            return slug, name, url
    return None


def id_name_map():
    """Map opaque id -> real slug, for building the (private) email summary."""
    return {anon_id(slug): slug for slug, _name, _url in slugged_databases()}


def leg(target):
    """Print real slug then url (two lines) for one database. Caller masks both."""
    found = _find(target)
    if not found:
        sys.stderr.write("ERROR: no external Postgres datastore for '%s'\n" % target)
        return 1
    slug, _name, url = found
    print(slug)
    print(url)
    return 0


def resolve(target):
    """Print only the connection_url for a target (id/slug/name). Caller masks it."""
    found = _find(target)
    if not found:
        sys.stderr.write("ERROR: no external Postgres datastore for '%s'\n" % target)
        return 1
    print(found[2])
    return 0


def name_of(target):
    """Print only the real slug for a target (id/slug/name). Caller masks it."""
    found = _find(target)
    if not found:
        sys.stderr.write("ERROR: no external Postgres datastore for '%s'\n" % target)
        return 1
    print(found[0])
    return 0


def build_matrix():
    only = os.environ.get("ONLY_APP", "").strip()
    excludes = load_excludes()
    include = []
    for slug, name, _url in slugged_databases():
        oid = anon_id(slug)
        # `only` may be given as the opaque id, the app name, or a specific slug.
        if only and only not in (oid, slug, name):
            continue
        if name in excludes or slug in excludes:
            sys.stderr.write("skip (excluded): %s\n" % oid)
            continue
        # The matrix carries ONLY the opaque id: no app name (public job names /
        # logs / artifacts must not reveal clients) and no db_url (a masked value
        # in a job output makes GitHub drop the whole output). Each backup leg
        # re-resolves its own slug+url from the id at runtime.
        include.append({"id": oid})

    include.sort(key=lambda d: d["id"])
    matrix = {"include": include}
    payload = json.dumps(matrix, separators=(",", ":"))

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write("matrix=%s\n" % payload)
            fh.write("count=%d\n" % len(include))
    # Human-readable echo: opaque ids only (real names are never logged).
    sys.stderr.write("discovered %d database(s): %s\n" % (
        len(include), ", ".join(d["id"] for d in include)))
    print(payload)
    return 0 if include else 1


def main(argv):
    if not TOKEN:
        sys.stderr.write("ERROR: APPLIKU_TOKEN is not set\n")
        return 2
    if not TEAM:
        sys.stderr.write("ERROR: APPLIKU_TEAM is not set\n")
        return 2
    if len(argv) >= 2 and argv[1] in ("--leg", "--resolve", "--name"):
        if len(argv) < 3:
            sys.stderr.write("usage: discover.py %s <id|slug|app>\n" % argv[1])
            return 2
        if argv[1] == "--leg":
            return leg(argv[2])
        if argv[1] == "--resolve":
            return resolve(argv[2])
        return name_of(argv[2])
    return build_matrix()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
