# =============================================================================
# sync-db.ps1  (run on your Windows workstation)
#
# Pulls the freshly-built fleet.db from the Linux control node down to this
# workstation, so the local conversational agent (mcp_server.py) answers from
# up-to-date data. Collection + build_db.py keep running on the control node;
# this just copies the resulting database over.
#
# Usage:
#   .\sync-db.ps1
#   .\sync-db.ps1 -ControlNode root@10.0.0.5 -RemotePath /opt/fleet-agent/data/fleet.db
# =============================================================================
param(
    [string]$ControlNode = "root@lasc-lxawx-01",
    [string]$RemotePath  = "/etc/ansible/fleet-agent/data/fleet.db"
)

$dest = Join-Path $PSScriptRoot "data\fleet.db"
New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null

Write-Host "Pulling ${ControlNode}:${RemotePath}"
Write-Host "     -> $dest"
scp "${ControlNode}:${RemotePath}" $dest

if ($LASTEXITCODE -eq 0) {
    Write-Host "Done. The agent will use the refreshed data on its next query." -ForegroundColor Green
} else {
    Write-Host "scp failed (exit $LASTEXITCODE). Check SSH access to $ControlNode." -ForegroundColor Red
}
