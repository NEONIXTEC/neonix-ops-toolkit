# nx_integrity.ps1 - shared helper (dot-source it): integrity level of a running process.
#
# Background: docs/WINDOWS_MEDIUM_PROCESS_OPS.md (a daily restart task skipped every High-integrity process)
# - I: a process id
# - O: 'Medium' | 'HIGH' | 'SYSTEM' | 'Low' | '?' (token not readable - from a Medium caller this usually
#      means the target is elevated, so callers treat '?' as suspicious)
# - Constraints: read-only (opens the process with PROCESS_QUERY_LIMITED_INFORMATION and its token with
#   TOKEN_QUERY, nothing else). ASCII only - a Korean Windows console is CP949.
#
#   . "$PSScriptRoot\nx_integrity.ps1"
#   Get-NxIntegrity 1234          -> 'Medium'
#   Get-NxProcAudit               -> objects { Level, Script, Pid, Session, Name } for python/py/pythonw/caddy

if (-not ('NxIntegrity' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class NxIntegrity {
    [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint a, bool i, int p);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    [DllImport("advapi32.dll")] static extern bool OpenProcessToken(IntPtr p, uint a, out IntPtr t);
    [DllImport("advapi32.dll")] static extern bool GetTokenInformation(IntPtr t, int c, IntPtr b, int l, out int r);
    [DllImport("advapi32.dll")] static extern IntPtr GetSidSubAuthorityCount(IntPtr s);
    [DllImport("advapi32.dll")] static extern IntPtr GetSidSubAuthority(IntPtr s, int i);
    public static string Level(int pid) {
        IntPtr h = OpenProcess(0x1000, false, pid);          // PROCESS_QUERY_LIMITED_INFORMATION
        if (h == IntPtr.Zero) return "?";
        IntPtr tok;
        try {
            if (!OpenProcessToken(h, 0x0008, out tok)) return "?";   // TOKEN_QUERY
            int need;
            GetTokenInformation(tok, 25, IntPtr.Zero, 0, out need);  // TokenIntegrityLevel
            IntPtr buf = Marshal.AllocHGlobal(need);
            try {
                if (!GetTokenInformation(tok, 25, buf, need, out need)) return "?";
                IntPtr sid = Marshal.ReadIntPtr(buf);
                int n = Marshal.ReadByte(GetSidSubAuthorityCount(sid));
                int rid = Marshal.ReadInt32(GetSidSubAuthority(sid, n - 1));
                if (rid >= 0x4000) return "SYSTEM";
                if (rid >= 0x3000) return "HIGH";
                if (rid >= 0x2000) return "Medium";
                return "Low";
            } finally { Marshal.FreeHGlobal(buf); CloseHandle(tok); }
        } finally { CloseHandle(h); }
    }
}
'@
}

function Get-NxIntegrity([int]$ProcessId) { return [NxIntegrity]::Level($ProcessId) }

function Get-NxProcAudit {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^(python|pythonw|py|caddy)\.exe$' } |
        ForEach-Object {
            $script = $_.Name
            if ($_.CommandLine -and $_.CommandLine -match '([\w\-]+\.py)') { $script = $Matches[1] }
            [pscustomobject]@{
                Level   = Get-NxIntegrity ([int]$_.ProcessId)
                Script  = $script
                Pid     = [int]$_.ProcessId
                Session = [int]$_.SessionId
                Name    = $_.Name
            }
        }
}
