$ErrorActionPreference = "Stop"
$OfficeDir = "C:\Users\Vlaslav\Downloads\GitHub_Control_Panel"
$StartupDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$BackupDir = Join-Path $OfficeDir "_legacy_startup_backup"

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

# Safely archive duplicate Virtual_Office\laptop_worker.py startup scripts
foreach ($name in @("launch_laptop_worker.vbs", "Start_Remote_Office_Silently.vbs", "Start_Virtual_Office_Worker.vbs")) {
    $src = Join-Path $StartupDir $name
    if (Test-Path $src) {
        Move-Item -Force -Path $src -Destination (Join-Path $BackupDir $name)
        Write-Host "Archived legacy startup launcher -> _legacy_startup_backup\$name"
    }
}

# Install priority startup launcher (00_ prefix ensures SEO runs FIRST at Windows logon, then spawns poller)
$SeoFirstSrc = Join-Path $OfficeDir "00_Start_Unity_SEO_First.vbs"
$SeoFirstDst = Join-Path $StartupDir "00_Start_Unity_SEO_First.vbs"
Copy-Item -Force -Path $SeoFirstSrc -Destination $SeoFirstDst
Write-Host "Installed priority Startup launcher -> $SeoFirstDst"

# Remove duplicate poller .vbs from Startup if present (00_Start_Unity_SEO_First.vbs starts SEO first, then spawns poller)
$OldPollerVbs = Join-Path $StartupDir "Start_Unity_Office_Poller_v34.vbs"
if (Test-Path $OldPollerVbs) {
    Move-Item -Force -Path $OldPollerVbs -Destination (Join-Path $BackupDir "Start_Unity_Office_Poller_v34.vbs")
}

# Optionally register Windows Scheduled Task ONLOGON (if allowed without elevation)
try {
    $Action = New-ScheduledTaskAction -Execute "C:\Users\Vlaslav\AppData\Local\Programs\Python\Python312\pythonw.exe" `
        -Argument "-X utf8 -u `"$OfficeDir\office_seo_worker.py`" --startup --then-start-poller" `
        -WorkingDirectory $OfficeDir
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName "Unity_Office_SEO_First_v34" -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
    Write-Host "Registered Windows Scheduled Task: Unity_Office_SEO_First_v34 (AtLogOn)"
} catch {
    Write-Host "Scheduled Task skipped (Startup VBS active): $($_.Exception.Message)"
}

Write-Host "ACTIVE STARTUP ITEMS:" ((Get-ChildItem $StartupDir -File).Name -join ", ")
