param([string]$TaskName = 'WinCopy', [string]$InstallDirectory = (Join-Path $env:USERPROFILE 'Documents\WinCopy'))
$ErrorActionPreference = 'Stop'
$state = Join-Path $InstallDirectory 'state'
if (Test-Path -LiteralPath $state) { New-Item -ItemType File -Path (Join-Path $state 'stop.flag') -Force | Out-Null }
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $TaskName
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Write-Output 'Startup task removed. Program, configuration and captured notes are preserved.'
