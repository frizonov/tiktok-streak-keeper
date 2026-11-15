# Registers background autostart for TikTok Streak Keeper (no console or browser windows):
#   1. Windows Startup registry entry (HKCU\...\Run) + starts background pythonw.exe daemon immediately
#   2. Windows Task Scheduler task (if permitted) as a secondary trigger
param([string]$Time)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Pyw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$Script = Join-Path $Root "streak.py"
$TaskName = "TikTokStreakKeeper"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"

if (-not (Test-Path $Pyw)) { throw "Virtual environment not found. Run .\setup.ps1 first." }

if (-not $Time) {
    $cfg = Get-Content (Join-Path $Root "config.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    $Time = $cfg.send_time
}

# 1. Registry autostart at Windows login (never requires Administrator privileges)
$cmd = "`"$Pyw`" `"$Script`" daemon"
Set-ItemProperty -Path $RunKey -Name $TaskName -Value $cmd

# Start background daemon right now (detached via WMI so it persists after shell exit)
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $cmd
    CurrentDirectory = $Root
} | Out-Null

# 2. Optional Scheduled Task (also launches daemon if not already running)
try {
    $action = New-ScheduledTaskAction -Execute $Pyw -Argument "`"$Script`" daemon" -WorkingDirectory $Root
    $daily = New-ScheduledTaskTrigger -Daily -At $Time
    $daily.Repetition = (New-ScheduledTaskTrigger -Once -At $Time `
        -RepetitionInterval (New-TimeSpan -Minutes 30) `
        -RepetitionDuration (New-TimeSpan -Hours 23)).Repetition
    $logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($daily, $logon) `
        -Settings $settings -Principal $principal -Force -ErrorAction Stop | Out-Null
} catch {
    # Registry Run + daemon already cover autostart without admin privileges
}

Write-Host "Autostart enabled (background service running, no windows)."
