# integrity_audit.ps1 - READ-ONLY: is any service process running elevated (or outside the desktop session)?
#
# - I: -Json (machine output for a watchdog)
# - O: one line per python/py/pythonw/caddy process + 'total=N bad=N'. Exit 0 = all Medium in session 1,
#      exit 3 = something is HIGH/SYSTEM/unreadable or not in session 1 (the daily restart would skip it),
#      exit 4 = the audit itself failed or saw no process at all (fail closed - "nothing seen" is not "all fine").
# - Changes nothing. ASCII only. Background: docs/WINDOWS_MEDIUM_PROCESS_OPS.md
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\windows-ops\integrity_audit.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\windows-ops\integrity_audit.ps1 -Json

param([switch]$Json)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
    . "$PSScriptRoot\nx_integrity.ps1"
    $null = Get-CimInstance Win32_OperatingSystem      # WMI must answer, or the empty list below means nothing
    $rows = @(Get-NxProcAudit | Sort-Object Level, Script)
} catch {
    'audit FAILED: {0}' -f $_.Exception.Message
    'total=0 bad=?'
    exit 4
}
$bad = @($rows | Where-Object { $_.Level -ne 'Medium' -or $_.Session -ne 1 })

if ($Json) {
    ConvertTo-Json -InputObject @{ rows = $rows; bad = $bad.Count; total = $rows.Count } -Depth 3 -Compress
} else {
    foreach ($r in $rows) {
        $mark = if ($r.Level -ne 'Medium' -or $r.Session -ne 1) { '!!' } else { '  ' }
        '{0} {1,-7} s{2} {3,-34} pid={4}' -f $mark, $r.Level, $r.Session, $r.Script, $r.Pid
    }
    'total={0} bad={1}' -f $rows.Count, $bad.Count
}
if ($bad.Count -gt 0) { exit 3 }
if ($rows.Count -eq 0) { exit 4 }      # a server with no python/caddy running is not healthy
exit 0
