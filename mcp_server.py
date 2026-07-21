#!/usr/bin/env python3
"""
mcp_server.py

The conversational AI-agent interface. This exposes the fleet database to
Claude (Claude Code, Claude Desktop, or any MCP client) as a set of read-only
tools. Once connected you can just ask, in plain English:

    "Which servers have CrowdStrike installed?"
    "List everything running an ssh service on Ubuntu boxes."
    "What OS is db01 on and when was it last collected?"

Claude picks the right tool below, runs it against real collected data, and
answers with actual server names and IPs -- it never guesses.

Run standalone (stdio transport):
    pip install -r requirements.txt
    python3 mcp_server.py

Register with Claude Code (on the control node):
    claude mcp add fleet -- python3 /path/to/fleet-agent/mcp_server.py

Security: every tool is READ-ONLY. The server can only SELECT from the local
snapshot database -- it has no SSH access and cannot change anything anywhere.
"""

import os
import sqlite3

from mcp.server.fastmcp import FastMCP

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("FLEET_DB", os.path.join(HERE, "data", "fleet.db"))

mcp = FastMCP("fleet-agent")


def _rows(sql, params=()):
    if not os.path.exists(DB_PATH):
        return {"error": f"Database not found at {DB_PATH}. "
                         f"Run collect_facts.yml then build_db.py."}
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


@mcp.tool()
def find_servers_by_package(term: str) -> list | dict:
    """Return servers that have an installed package whose name matches `term`
    (substring, case-insensitive). Use this for questions like 'which servers
    have crowdstrike/falcon/nginx/openssl installed'. Returns hostname,
    primary_ip, matched package name and version."""
    return _rows(
        """SELECT DISTINCT h.hostname, h.primary_ip, p.name AS package, p.version
           FROM hosts h JOIN packages p ON p.hostname = h.hostname
           WHERE p.name LIKE ? ORDER BY h.hostname""",
        (f"%{term}%",),
    )


@mcp.tool()
def find_servers_by_service(term: str) -> list | dict:
    """Return servers that have a service whose name matches `term`, with its
    running state. Use for 'which servers run the falcon-sensor / sshd / docker
    service'. Returns hostname, primary_ip, service name and state."""
    return _rows(
        """SELECT DISTINCT h.hostname, h.primary_ip, s.name AS service, s.state
           FROM hosts h JOIN services s ON s.hostname = h.hostname
           WHERE s.name LIKE ? ORDER BY h.hostname""",
        (f"%{term}%",),
    )


@mcp.tool()
def get_host(name: str) -> list | dict:
    """Return full detail for a host matching `name` (hostname or inventory
    name, substring match): IPs, OS/distro/version, kernel, and when it was
    last collected."""
    return _rows(
        "SELECT * FROM hosts WHERE hostname LIKE ? OR inventory_hostname LIKE ?",
        (f"%{name}%", f"%{name}%"),
    )


@mcp.tool()
def list_hosts() -> list | dict:
    """Return every server the agent knows about: hostname, primary_ip,
    distribution and version. Use for 'how many servers / list all servers'."""
    return _rows(
        "SELECT hostname, primary_ip, distribution, distribution_version "
        "FROM hosts ORDER BY hostname"
    )


@mcp.tool()
def disk_usage(mount: str = "", min_pct_used: float = 0.0) -> list | dict:
    """Return filesystem usage per host. Use for 'how much space is free in
    /var', 'which servers are low on disk', 'show mounts over 90% full'.
    `mount` filters to matching mount paths (e.g. '/var'); `min_pct_used` keeps
    only mounts at/above that percent full. Sizes are bytes: divide by 1024**3
    for GB. size_available is the free space. Values reflect the last
    collection run, not this instant (disk usage drifts slowly, so this is
    usually fine)."""
    sql = (
        "SELECT h.hostname, h.primary_ip, m.mount, m.fstype, m.size_total, "
        "m.size_available, m.pct_used, h.collected_at "
        "FROM hosts h JOIN mounts m ON m.hostname = h.hostname WHERE 1=1"
    )
    params = []
    if mount:
        sql += " AND m.mount LIKE ?"
        params.append(f"%{mount}%")
    if min_pct_used:
        sql += " AND m.pct_used >= ?"
        params.append(min_pct_used)
    sql += " ORDER BY m.pct_used DESC, h.hostname"
    return _rows(sql, tuple(params))


@mcp.tool()
def memory_usage() -> list | dict:
    """Return memory and swap per host in MB. Use for 'how much RAM do servers
    have', 'which servers have the least free memory'. IMPORTANT: mem_total_mb
    and swap_total_mb are accurate (static hardware), but mem_free_mb is only a
    point-in-time SAMPLE taken during the last collection run -- it is NOT the
    live free memory right now. For real-time free RAM, a live refresh or a
    monitoring system is needed. Includes collected_at so you can state how old
    the sample is."""
    return _rows(
        "SELECT hostname, primary_ip, mem_total_mb, mem_free_mb, "
        "swap_total_mb, swap_free_mb, collected_at FROM hosts "
        "ORDER BY mem_free_mb ASC"
    )


@mcp.tool()
def os_breakdown() -> list | dict:
    """Return a count of servers grouped by distribution and version."""
    return _rows(
        "SELECT distribution, distribution_version, COUNT(*) AS count "
        "FROM hosts GROUP BY distribution, distribution_version ORDER BY count DESC"
    )


@mcp.tool()
def run_select(sql: str) -> list | dict:
    """Escape hatch for arbitrary read-only questions. Runs a single SELECT
    against the fleet database. Tables: hosts(hostname, primary_ip, all_ips,
    os_family, distribution, distribution_version, kernel, architecture,
    mem_total_mb, mem_free_mb, swap_total_mb, swap_free_mb, collected_at),
    packages(hostname, name, version, arch),
    services(hostname, name, state, status, source),
    mounts(hostname, mount, device, fstype, size_total, size_available,
    pct_used). size_* are bytes. Only SELECT is permitted."""
    if not sql.strip().lower().startswith("select"):
        return {"error": "Only read-only SELECT statements are allowed."}
    return _rows(sql)


if __name__ == "__main__":
    mcp.run()
