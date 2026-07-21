# fleet-agent

An interactive AI agent that knows every Linux server in your environment and
answers plain-English questions about them — for example:

> **You:** Which servers have CrowdStrike installed?
> **Agent:** 3 servers have `falcon-sensor` installed:
> | hostname | ip | version |
> |---|---|---|
> | web01.ali.local | 10.0.10.11 | 7.14.0-16903 |
> | db01.ali.local  | 10.0.20.5  | 7.14.0-16903 |
> | app-eu-01       | 10.2.0.10  | 7.13.0-16805 |

## How it works

The AI never guesses and never logs into your servers directly. It answers from
**real facts collected by Ansible** and stored locally, so answers are
trustworthy and the AI's access stays read-only.

```
[ Linux fleet ]
      │  Ansible over SSH (read-only fact gathering)
      ▼
collect_facts.yml  ─►  data/facts/<host>.json   (one snapshot per host)
      │  build_db.py
      ▼
data/fleet.db       (SQLite: hosts / packages / services)
      ▲
      │  read-only queries
  ┌───┴─────────────┐
  query.py          mcp_server.py
  (instant CLI)     (Claude / AI-agent chat interface)
```

- **Collection** — `collect_facts.yml` gathers system facts, every installed
  package, and every service from each host. It only reads; nothing on the
  targets is modified.
- **Store** — `build_db.py` folds the snapshots into `data/fleet.db`.
- **Answers** — `query.py` gives instant deterministic answers with no AI
  needed. `mcp_server.py` exposes the same data to Claude as read-only tools so
  you can just chat.

## Prerequisites (on the control node / jump host)

| Requirement | Notes |
|---|---|
| Ansible | `python3 -m pip install ansible-core` (or distro package) |
| SSH reachability to every server | The control node must be able to SSH in |
| A read-capable service account | Set `ansible_user` in `inventory/hosts.ini`; sudo needed for package/service facts on some distros |
| Python 3.6+ | For `build_db.py` / `query.py` (stdlib only) |
| `pip install -r requirements.txt` | **Only** for the AI chat interface (`mcp_server.py`) |

## Setup

### 1. Populate the inventory
Edit [`inventory/hosts.ini`](inventory/hosts.ini) and paste your real servers.
This is the one manual step — the master list of what the agent knows.

### 2. Test connectivity
```bash
ansible -i inventory/hosts.ini all -m ping
```

### 3. Collect facts + build the database
```bash
ansible-playbook -i inventory/hosts.ini collect_facts.yml
python3 build_db.py
```

### 4. Ask questions (no AI required)
```bash
python3 query.py package falcon        # CrowdStrike / falcon-sensor
python3 query.py service falcon-sensor
python3 query.py host db01
python3 query.py os
python3 query.py sql "SELECT hostname, primary_ip FROM hosts WHERE distribution='Ubuntu'"
```

### 5. Turn on the conversational AI agent (optional)
```bash
pip install -r requirements.txt
# Register with Claude Code on the control node:
claude mcp add fleet -- python3 /opt/fleet-agent/mcp_server.py
```
Then just chat: *"which servers have crowdstrike installed?"*, *"list all
servers running docker"*, *"how many RHEL 8 boxes do we have?"*

### 6. Keep it fresh (scheduling)
Because you chose the snapshot model, answers are as current as the last
collection run. Schedule it (examples in [`schedule/`](schedule/)):

- **cron** — `schedule/collect-facts.cron`
- **systemd** — `schedule/fleet-collect.service` + `.timer`

Daily is a sensible default; tighten to hourly for fast-moving fleets.

## What this can answer today

Anything derivable from installed packages, services, and system facts:
CrowdStrike / any agent or package presence, service run-state, OS & version
breakdowns, kernel versions, IP lookups, per-host detail. The `run_select` tool
and `query.py sql` cover arbitrary read-only questions over the same data.

## Extending it

- **More facts** — add tasks to `collect_facts.yml` (e.g. mounted disks, open
  ports, listening sockets, specific config file contents) and matching columns
  in `build_db.py`.
- **Live "refresh now"** — add a tool that runs `collect_facts.yml --limit
  <host>` on demand for a single box, then rebuilds. (This is the hybrid model;
  gate it behind approval since it touches live servers.)
- **Web UI / Slack** — the same MCP tools or `query.py` can back a chat bot.

## Security notes

- Collection and every query are **read-only**. The AI layer talks only to the
  local SQLite file — it has no SSH credentials and cannot change servers.
- Keep SSH/become credentials in an Ansible **vault**, never in `hosts.ini`
  (same pattern as your existing `ad-domain-join/group_vars/all/vault.yml`).
- Restrict who can read `data/fleet.db` — it contains a full software inventory.
```
