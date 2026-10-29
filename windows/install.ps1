param(
    [string]$OutputDirectory = (Join-Path $env:USERPROFILE 'Documents\Win-Copy'),
    [string]$Python = 'python',
    [string]$TaskName = 'WinCopy',
    [string]$InstallDirectory = (Join-Path $env:USERPROFILE 'Documents\WinCopy')
)
$ErrorActionPreference = 'Stop'
$pythonPath = (& $Python -c 'import sys; print(sys.executable)').Trim()
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ is required.' }
$pythonw = Join-Path (Split-Path $pythonPath) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { throw 'pythonw.exe is required for hidden startup.' }
if (-not [System.IO.Path]::IsPathRooted($OutputDirectory)) { throw 'OutputDirectory must be absolute.' }
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) { throw "Task $TaskName already exists. Uninstall its launcher first; captures are preserved." }
$app = Join-Path $InstallDirectory 'app'
$state = Join-Path $InstallDirectory 'state'
New-Item -ItemType Directory -Path $app,$state,$OutputDirectory -Force | Out-Null
if (Test-Path -LiteralPath (Join-Path $state 'stop.flag')) { Remove-Item -LiteralPath (Join-Path $state 'stop.flag') }
$source = Split-Path $PSScriptRoot
Copy-Item -LiteralPath (Join-Path $source 'privacy-rules.json') -Destination $app
$win = Join-Path $app 'windows'
New-Item -ItemType Directory -Path $win -Force | Out-Null
Get-ChildItem -LiteralPath $PSScriptRoot -File | Where-Object { $_.Extension -in '.py','.ps1','.md' } | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $win }
$config = Join-Path $InstallDirectory 'config.json'
@{output_dir=$OutputDirectory; state_dir=$state} | ConvertTo-Json | Set-Content -LiteralPath $config -Encoding UTF8
$script = Join-Path $win 'win_copy.py'
$action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"{0}" supervise --config "{1}"' -f $script,$config)
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$logon = New-ScheduledTaskTrigger -AtLogOn -User $userId
# Watchdog insurance: a missed/clean exit is also recovered, not just crash codes.
$periodic = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$task = New-ScheduledTask -Action $action -Trigger @($logon,$periodic) -Principal $principal -Settings $settings -Description 'Local text clipboard journal with conservative filtering. No network. Login startup and crash recovery.'
Register-ScheduledTask -TaskName $TaskName -InputObject $task | Out-Null
Start-ScheduledTask -TaskName $TaskName
[pscustomobject]@{Task=$TaskName;Config=$config;Output=$OutputDirectory;Python=$pythonw} | ConvertTo-Json
