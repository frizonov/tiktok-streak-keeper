$TaskName = "TikTokStreakKeeper"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $Root ".daemon.pid"

Remove-ItemProperty -Path $RunKey -Name $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

if (Test-Path $PidFile) {
    $procId = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($procId) { Stop-Process -Id ([int]$procId) -Force -ErrorAction SilentlyContinue }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

Write-Host "Autostart disabled and background daemon stopped."
