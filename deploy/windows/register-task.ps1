<#
.SYNOPSIS
  Register a Windows Scheduled Task that runs snapshot.ps1 every night.

.DESCRIPTION
  Creates (or replaces) a daily task. By default it runs as the current user
  and only fires while you are logged on — with -StartWhenAvailable it catches
  up the next time you log in if the PC was off. Use -RunWhenLoggedOff to run
  even when signed out (stores the account password; also needed for reliable
  access to a network share at night).

.EXAMPLE
  .\register-task.ps1
  .\register-task.ps1 -Time 02:00 -OutDir '\\fileserver\stockwatch\baselines' -RunWhenLoggedOff
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'StockWatch Nightly Snapshot',
    [string]$Time = '02:00',
    [string]$OutDir,
    # Run even when no one is logged on (prompts for the account password).
    [switch]$RunWhenLoggedOff
)

$ErrorActionPreference = 'Stop'
# $PSScriptRoot is unreliable in param() defaults under `-File`; resolve here.
$here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Definition }
$script = (Resolve-Path (Join-Path $here 'snapshot.ps1')).Path
if (-not $OutDir) { $OutDir = Join-Path $here '..\..\baselines' }
$OutDir = [System.IO.Path]::GetFullPath(
    [System.IO.Path]::Combine((Get-Location).Path, $OutDir))

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ("-NoProfile -ExecutionPolicy Bypass -File `"{0}`" -OutDir `"{1}`"" -f $script, $OutDir)
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
# Catch up if the PC was off at the scheduled time; give each run up to an hour.
# -AllowStartIfOnBatteries / -DontStopIfGoingOnBatteries: the defaults REFUSE to
# start on battery and kill a running task when unplugged, so a laptop misses
# every 02:00 run. Override both so the snapshot fires regardless of power state.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

if ($RunWhenLoggedOff) {
    $cred = Get-Credential -Message 'Windows account to run the task as (password is stored so it can run while signed out and reach network shares)'
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -User $cred.UserName `
        -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null
} else {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Force | Out-Null
}

Write-Host "Registered '$TaskName': daily at $Time  ->  $OutDir"
Write-Host "Run it now to test:  Start-ScheduledTask -TaskName '$TaskName'"
