#!/usr/bin/env python3
"""
build_db.py

Fold the per-host JSON snapshots produced by collect_facts.yml into a single
queryable SQLite database (data/fleet.db).

The database is rebuilt from scratch on every run so it always reflects the
latest snapshot set -- there is no stale/merge state to reason about.

Tables:
  hosts     (hostname, inventory_hostname, primary_ip, all_ips, os_family,
             distribution, distribution_version, kernel, architecture,
             collected_at)
  packages  (hostname, name, version, arch)
  services  (hostname, name, state, status, source)

Usage:
  python3 build_db.py                 # reads data/facts/, writes data/fleet.db
  python3 build_db.py --facts DIR --db PATH
"""

import argparse
import json
import os
import sqlite3
import sys
from glob import glob

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FACTS = os.path.join(HERE, "data", "facts")
DEFAULT_DB = os.path.join(HERE, "data", "fleet.db")

SCHEMA = """
DROP TABLE IF EXISTS hosts;
DROP TABLE IF EXISTS packages;
DROP TABLE IF EXISTS services;
DROP TABLE IF EXISTS mounts;

CREATE TABLE hosts (
    hostname             TEXT PRIMARY KEY,
    inventory_hostname   TEXT,
    primary_ip           TEXT,
    all_ips              TEXT,
    os_family            TEXT,
    distribution         TEXT,
    distribution_version TEXT,
    kernel               TEXT,
    architecture         TEXT,
    mem_total_mb         INTEGER,
    mem_free_mb          INTEGER,
    swap_total_mb        INTEGER,
    swap_free_mb         INTEGER,
    collected_at         TEXT
);

CREATE TABLE packages (
    hostname TEXT,
    name     TEXT,
    version  TEXT,
    arch     TEXT
);

CREATE TABLE services (
    hostname TEXT,
    name     TEXT,
    state    TEXT,
    status   TEXT,
    source   TEXT
);

CREATE TABLE mounts (
    hostname       TEXT,
    mount          TEXT,
    device         TEXT,
    fstype         TEXT,
    size_total     INTEGER,   -- bytes
    size_available INTEGER,   -- bytes
    pct_used       REAL       -- 0-100
);

CREATE INDEX idx_pkg_name ON packages(name);
CREATE INDEX idx_pkg_host ON packages(hostname);
CREATE INDEX idx_svc_name ON services(name);
CREATE INDEX idx_svc_host ON services(hostname);
CREATE INDEX idx_mnt_host ON mounts(hostname);
CREATE INDEX idx_mnt_mount ON mounts(mount);
"""


def _int(value):
    """Best-effort int coercion; Ansible often hands us numeric strings."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def load_snapshot(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build(facts_dir, db_path):
    snapshots = sorted(glob(os.path.join(facts_dir, "*.json")))
    if not snapshots:
        print(
            f"No snapshots found in {facts_dir}. "
            f"Run collect_facts.yml first.",
            file=sys.stderr,
        )
        return 1

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    host_rows = 0
    pkg_rows = 0
    svc_rows = 0
    mnt_rows = 0

    for path in snapshots:
        try:
            snap = load_snapshot(path)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  ! skipping {os.path.basename(path)}: {exc}", file=sys.stderr)
            continue

        hostname = snap.get("hostname") or snap.get("inventory_hostname")
        conn.execute(
            """INSERT OR REPLACE INTO hosts
               (hostname, inventory_hostname, primary_ip, all_ips, os_family,
                distribution, distribution_version, kernel, architecture,
                mem_total_mb, mem_free_mb, swap_total_mb, swap_free_mb,
                collected_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                hostname,
                snap.get("inventory_hostname", ""),
                snap.get("primary_ip", ""),
                ", ".join(snap.get("all_ipv4", []) or []),
                snap.get("os_family", ""),
                snap.get("distribution", ""),
                snap.get("distribution_version", ""),
                snap.get("kernel", ""),
                snap.get("architecture", ""),
                _int(snap.get("mem_total_mb")),
                _int(snap.get("mem_free_mb")),
                _int(snap.get("swap_total_mb")),
                _int(snap.get("swap_free_mb")),
                snap.get("collected_at", ""),
            ),
        )
        host_rows += 1

        for pkg in snap.get("packages", []) or []:
            if not isinstance(pkg, dict):
                continue
            conn.execute(
                "INSERT INTO packages (hostname, name, version, arch) "
                "VALUES (?,?,?,?)",
                (hostname, pkg.get("name", ""), pkg.get("version", ""),
                 pkg.get("arch", "")),
            )
            pkg_rows += 1

        for svc in snap.get("services", []) or []:
            if not isinstance(svc, dict):
                continue
            conn.execute(
                "INSERT INTO services (hostname, name, state, status, source) "
                "VALUES (?,?,?,?,?)",
                (hostname, svc.get("name", ""), svc.get("state", ""),
                 svc.get("status", ""), svc.get("source", "")),
            )
            svc_rows += 1

        for mnt in snap.get("mounts", []) or []:
            if not isinstance(mnt, dict):
                continue
            total = _int(mnt.get("size_total"))
            avail = _int(mnt.get("size_available"))
            pct = round((total - avail) / total * 100, 1) if total else None
            conn.execute(
                """INSERT INTO mounts
                   (hostname, mount, device, fstype, size_total,
                    size_available, pct_used)
                   VALUES (?,?,?,?,?,?,?)""",
                (hostname, mnt.get("mount", ""), mnt.get("device", ""),
                 mnt.get("fstype", ""), total, avail, pct),
            )
            mnt_rows += 1

    conn.commit()
    conn.close()

    print(
        f"Built {db_path}\n"
        f"  hosts:    {host_rows}\n"
        f"  packages: {pkg_rows}\n"
        f"  services: {svc_rows}\n"
        f"  mounts:   {mnt_rows}"
    )
    return 0


def main():
    ap = argparse.ArgumentParser(description="Build fleet.db from fact snapshots.")
    ap.add_argument("--facts", default=DEFAULT_FACTS, help="snapshot dir")
    ap.add_argument("--db", default=DEFAULT_DB, help="output sqlite path")
    args = ap.parse_args()
    sys.exit(build(args.facts, args.db))


if __name__ == "__main__":
    main()
