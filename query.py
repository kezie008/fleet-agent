#!/usr/bin/env python3
"""
query.py

Instant answers over the collected fleet database -- no AI required. This is
the deterministic layer the AI agent sits on top of; every answer the agent
gives ultimately comes from queries like these against real collected data.

Examples:
  # Which servers have CrowdStrike installed?
  python3 query.py package falcon

  # Which servers are running the falcon-sensor service?
  python3 query.py service falcon-sensor

  # Everything known about one host
  python3 query.py host web01.ali.local

  # Group the fleet by OS
  python3 query.py os

  # Escape hatch: any read-only SQL
  python3 query.py sql "SELECT hostname, primary_ip FROM hosts WHERE distribution='Ubuntu'"

Add --json to any command for machine-readable output.
"""

import argparse
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "data", "fleet.db")


def connect(db_path):
    if not os.path.exists(db_path):
        sys.exit(
            f"Database not found: {db_path}\n"
            f"Run collect_facts.yml then build_db.py first."
        )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def emit(rows, headers, as_json):
    dicts = [dict(r) for r in rows]
    if as_json:
        print(json.dumps(dicts, indent=2))
        return
    if not dicts:
        print("(no matches)")
        return
    widths = {h: max(len(h), *(len(str(d.get(h, ""))) for d in dicts)) for h in headers}
    print("  ".join(h.upper().ljust(widths[h]) for h in headers))
    print("  ".join("-" * widths[h] for h in headers))
    for d in dicts:
        print("  ".join(str(d.get(h, "")).ljust(widths[h]) for h in headers))
    print(f"\n{len(dicts)} row(s)")


def cmd_package(conn, args):
    rows = conn.execute(
        """SELECT DISTINCT h.hostname, h.primary_ip, p.name AS package, p.version
           FROM hosts h JOIN packages p ON p.hostname = h.hostname
           WHERE p.name LIKE ?
           ORDER BY h.hostname""",
        (f"%{args.term}%",),
    ).fetchall()
    emit(rows, ["hostname", "primary_ip", "package", "version"], args.json)


def cmd_service(conn, args):
    rows = conn.execute(
        """SELECT DISTINCT h.hostname, h.primary_ip, s.name AS service, s.state
           FROM hosts h JOIN services s ON s.hostname = h.hostname
           WHERE s.name LIKE ?
           ORDER BY h.hostname""",
        (f"%{args.term}%",),
    ).fetchall()
    emit(rows, ["hostname", "primary_ip", "service", "state"], args.json)


def cmd_host(conn, args):
    host = conn.execute(
        "SELECT * FROM hosts WHERE hostname LIKE ? OR inventory_hostname LIKE ?",
        (f"%{args.name}%", f"%{args.name}%"),
    ).fetchall()
    emit(host, ["hostname", "primary_ip", "distribution",
                "distribution_version", "kernel", "collected_at"], args.json)


def _gb(num_bytes):
    try:
        return f"{int(num_bytes) / (1024 ** 3):.1f}"
    except (TypeError, ValueError):
        return ""


def cmd_disk(conn, args):
    sql = (
        "SELECT h.hostname, h.primary_ip, m.mount, m.fstype, "
        "m.size_total, m.size_available, m.pct_used "
        "FROM hosts h JOIN mounts m ON m.hostname = h.hostname WHERE 1=1"
    )
    params = []
    if args.mount:
        sql += " AND m.mount LIKE ?"
        params.append(f"%{args.mount}%")
    if args.min_pct is not None:
        sql += " AND m.pct_used >= ?"
        params.append(args.min_pct)
    sql += " ORDER BY m.pct_used DESC, h.hostname"
    rows = conn.execute(sql, params).fetchall()
    # Present bytes as human GB.
    pretty = []
    for r in rows:
        d = dict(r)
        d["total_gb"] = _gb(d.pop("size_total"))
        d["free_gb"] = _gb(d.pop("size_available"))
        pretty.append(d)
    emit(pretty, ["hostname", "primary_ip", "mount",
         "fstype", "total_gb", "free_gb", "pct_used"], args.json)


def cmd_mem(conn, args):
    rows = conn.execute(
        "SELECT hostname, primary_ip, mem_total_mb, mem_free_mb, "
        "swap_total_mb, swap_free_mb, collected_at FROM hosts "
        "ORDER BY mem_free_mb ASC"
    ).fetchall()
    emit(rows, ["hostname", "primary_ip", "mem_total_mb", "mem_free_mb",
                "swap_total_mb", "swap_free_mb", "collected_at"], args.json)


def cmd_os(conn, args):
    rows = conn.execute(
        """SELECT distribution, distribution_version, COUNT(*) AS count
           FROM hosts GROUP BY distribution, distribution_version
           ORDER BY count DESC""",
    ).fetchall()
    emit(rows, ["distribution", "distribution_version", "count"], args.json)


def cmd_sql(conn, args):
    q = args.query.strip()
    if not q.lower().startswith("select"):
        sys.exit("Only read-only SELECT statements are allowed.")
    try:
        rows = conn.execute(q).fetchall()
    except sqlite3.Error as exc:
        sys.exit(f"SQL error: {exc}")
    headers = list(rows[0].keys()) if rows else []
    emit(rows, headers, args.json)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--json", action="store_true", help="output JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("package", help="find hosts with a matching package")
    p.add_argument("term")
    p.set_defaults(func=cmd_package)

    p = sub.add_parser("service", help="find hosts with a matching service")
    p.add_argument("term")
    p.set_defaults(func=cmd_service)

    p = sub.add_parser("host", help="show details for a host")
    p.add_argument("name")
    p.set_defaults(func=cmd_host)

    p = sub.add_parser("disk", help="filesystem usage across the fleet")
    p.add_argument("mount", nargs="?", default=None,
                   help="filter to mounts matching this path, e.g. /var")
    p.add_argument("--min-pct", type=float, default=None, dest="min_pct",
                   help="only show mounts at/above this %% used")
    p.set_defaults(func=cmd_disk)

    p = sub.add_parser("mem", help="memory/swap per host (as of last collection)")
    p.set_defaults(func=cmd_mem)

    p = sub.add_parser("os", help="count hosts by OS/version")
    p.set_defaults(func=cmd_os)

    p = sub.add_parser("sql", help="run a read-only SELECT")
    p.add_argument("query")
    p.set_defaults(func=cmd_sql)

    args = ap.parse_args()
    conn = connect(args.db)
    args.func(conn, args)


if __name__ == "__main__":
    main()
