<#
.SYNOPSIS
  Capture the three iSync stock baselines (rm/fg/wip) to a folder.

.DESCRIPTION
  Wraps `stockwatch snapshot-all` for a scheduled nightly run. Finds the
  stockwatch executable in the project's virtual environment, writes the
  baseline CSVs to -OutDir, and appends a dated log to <OutDir>\_logs.
  Exits with stockwatch's own exit code so Task Scheduler sees failures.

.EXAMPLE
  .\snapshot.ps1
  .\snapshot.ps1 -OutDir '\\fileserver\stockwatch\baselines'
#>
[CmdletBinding()]
param(
    # Where the baseline CSVs land. Local folder or a UNC share.
    [string]$OutDir = (Join-Path $PSScriptRoot '..\..\baselines'),
    # Project checkout root (defaults to two levels up from this script).
    [string]$ProjectRoot = (Join-Path $PSScriptRoot '..\..'),
    # Path to config\tables.yml (defaults to the one in the checkout).
    [string]$Config,
    # Override the stockwatch.exe path if it isn't in a standard venv.
    [string]$StockwatchExe
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path $ProjectRoot).Path

# Locate the stockwatch executable: venv first, then PATH.
if (-not $StockwatchExe) {
    $candidates = @(
        (Join-Path $ProjectRoot '.venv\Scripts\stockwatch.exe'),
        (Join-Path $ProjectRoot 'venv\Scripts\stockwatch.exe')
    )
    $StockwatchExe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $StockwatchExe) { $StockwatchExe = 'stockwatch' }  # fall back to PATH
}

if (-not $Config) { $Config = Join-Path $ProjectRoot 'config\tables.yml' }

# Resolve OutDir to an absolute path and make sure it (and the log dir) exist.
$OutDir = [System.IO.Path]::GetFullPath(
    [System.IO.Path]::Combine((Get-Location).Path, $OutDir))
$logDir = Join-Path $OutDir '_logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ('snapshot-{0:yyyyMMdd}.log' -f (Get-Date))

"=== {0:s}  snapshot-all -> {1} ===" -f (Get-Date), $OutDir |
    Tee-Object -FilePath $log -Append

& $StockwatchExe snapshot-all --out-dir $OutDir --config $Config *>&1 |
    Tee-Object -FilePath $log -Append
$code = $LASTEXITCODE

"=== exit {0} ===" -f $code | Tee-Object -FilePath $log -Append
exit $code
