# restart_one_medium.ps1 - restart ONE launcher process (collector, worker, bot, ...) from an SSH/agent shell
# WITHOUT ever creating an elevated process.
#
# Background: docs/WINDOWS_MEDIUM_PROCESS_OPS.md (High-integrity python = the daily full restart skips everything)
# - I: -Script <leaf of a launch line your launcher knows>, e.g. worker.py, collector.py
#      -Launcher <your desktop launch tool, default scripts\launch_one.ps1>. Contract: `-Script X -DryRun` exits 0
#      when it knows X; `-Script X` (re)starts it and appends "[launch_one] X up pid=N" (or NOT UP / ABORT) to -LogFile.
# - O: exit 0 only when the launcher logged "<leaf> up pid=N" AND the new process is Medium integrity
#      in session 1 (the logged-in desktop). Anything else exits 1 with the reason.
# - Constraints:
#     * An OpenSSH shell on Windows is elevated and runs in session 0. Starting python from it directly gives a
#       High-integrity, invisible process. So this script never starts anything itself: it registers a one-shot
#       scheduled task (LogonType=Interactive, RunLevel=Limited) that runs the desktop launch tool
#       inside the user's desktop session - the same trick as restart_app_medium.ps1 (measured in production).
#     * app.py is refused - it has its own standby handover behind Caddy (restart_app_medium.ps1).
#     * Agents call it only through agent_ship.py restart-proc after the owner approves.
#     * ASCII output only (a Korean Windows console is CP949).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\windows-ops\restart_one_medium.ps1 -Script worker.py

param(
    [Parameter(Mandatory = $true)][string]$Script,
    [int]$TimeoutSec = 90,
    [string]$Launcher = '',
    [string]$LogFile = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($env:SSH_CONNECTION) { try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { } }
$Root = (Resolve-Path "$PSScriptRoot\..").Path
$Launch = if ($Launcher) { $Launcher } else { Join-Path $Root 'scripts\launch_one.ps1' }
if (-not $LogFile) { $LogFile = Join-Path $Root 'Startup_log.txt' }
$TASK = 'TMP_RestartOneMedium'
. "$PSScriptRoot\nx_integrity.ps1"

function Fail([string]$msg) { Write-Host "[restart_one] FAIL $msg"; exit 1 }

if ($Script -notmatch '^[\w\-]+\.py$') { Fail "bad script name '$Script'" }
if ($Script -ieq 'app.py') { Fail 'app.py goes through restart_app_medium.ps1 (standby handover), not here' }
if (-not (Test-Path $Launch)) { Fail "missing $Launch" }

# 1) the launcher must know the name; also shows what is running now (dry run touches nothing)
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Launch -Script $Script -DryRun
if ($LASTEXITCODE -ne 0) { Fail "'$Script' is not a launch line (see the list above)" }

$esc = [regex]::Escape($Script)
function Get-Main {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match '^(python|pythonw)\.exe$' -and $_.CommandLine -and
        $_.CommandLine -match "(^|[\\/""'\s])$esc(""|'|\s|$)" })
}
$oldIds = @(Get-Main | ForEach-Object { [int]$_.ProcessId })
$logStart = if (Test-Path $LogFile) { (Get-Item $LogFile).Length } else { 0 }
Write-Host ("[restart_one] {0}: running pid(s) {1}" -f $Script, $(if ($oldIds.Count) { $oldIds -join ',' } else { '-' }))

# 2) one-shot Interactive + Limited task -> launch_one runs in session 1 with a Medium token
Unregister-ScheduledTask -TaskName $TASK -Confirm:$false -ErrorAction SilentlyContinue
$arg = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Script {1}' -f $Launch, $Script
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg -WorkingDirectory $Root
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TASK -Action $action -Principal $principal | Out-Null
Write-Host '[restart_one] starting launch_one via scheduled task (Interactive, Limited)...'
Start-ScheduledTask -TaskName $TASK

# 3) wait for launch_one's own verdict line in the startup log
$verdict = ''
$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline -and -not $verdict) {
    Start-Sleep -Seconds 2
    if (-not (Test-Path $LogFile)) { continue }
    try {
        $fs = [IO.File]::Open($LogFile, 'Open', 'Read', 'ReadWrite')
        try {
            [void]$fs.Seek($logStart, 'Begin')
            $sr = New-Object IO.StreamReader($fs, [Text.Encoding]::UTF8)
            $new = $sr.ReadToEnd()
        } finally { $fs.Close() }
    } catch { continue }
    foreach ($ln in ($new -split "`r?`n")) {
        if ($ln -match "\[launch_one\] $esc (up pid=\d+.*|NOT UP.*|ABORT.*|DUPLICATE.*)") { $verdict = $Matches[1] }
    }
}
Unregister-ScheduledTask -TaskName $TASK -Confirm:$false -ErrorAction SilentlyContinue
if (-not $verdict) { Fail "no verdict from launch_one in $TimeoutSec s - look at the server desktop window" }
Write-Host "[restart_one] launch_one: $Script $verdict"
if ($verdict -notmatch '^up pid=') { Fail "launch_one did not bring $Script up" }

# 4) the new process must be Medium in session 1, and the old ones gone
$now = @(Get-Main)
$fresh = @($now | Where-Object { $oldIds -notcontains [int]$_.ProcessId })
$stale = @($now | Where-Object { $oldIds -contains [int]$_.ProcessId })
if ($stale.Count -gt 0) { Fail ("old pid(s) still alive: {0}" -f (($stale | ForEach-Object { $_.ProcessId }) -join ',')) }
if ($fresh.Count -lt 1) { Fail 'no new process found after launch' }
$bad = 0
foreach ($p in $fresh) {
    $lvl = Get-NxIntegrity ([int]$p.ProcessId)
    Write-Host ("[restart_one] new {0} pid={1} integrity={2} session={3}" -f $p.Name, $p.ProcessId, $lvl, $p.SessionId)
    if ($lvl -ne 'Medium' -or [int]$p.SessionId -ne 1) { $bad++ }
}
if ($bad -gt 0) { Fail 'new process is not Medium/session 1 - close it from the desktop and restart it there' }
Write-Host "[restart_one] OK $Script restarted (Medium, session 1)"
exit 0
