# Finance Paper Agent - Windows Task Scheduler setup
# Run once as Administrator

$python  = "C:\Users\miaoy\AppData\Local\Programs\Python\Python311\python.exe"
$script  = "C:\Users\miaoy\Documents\finance-paper-agent\main.py"
$workDir = "C:\Users\miaoy\Documents\finance-paper-agent"

# Task 1: Prepare (at logon)
$prepareAction  = New-ScheduledTaskAction -Execute $python -Argument "`"$script`" --prepare" -WorkingDirectory $workDir
$prepareTrigger = New-ScheduledTaskTrigger -AtLogOn
$prepareSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew -StartWhenAvailable

Register-ScheduledTask `
    -TaskName    "FinancePaperAgent-Prepare" `
    -Action      $prepareAction `
    -Trigger     $prepareTrigger `
    -Settings    $prepareSettings `
    -RunLevel    Limited `
    -Description "Fetch and cache weekly QIS paper digest at logon" `
    -Force

Write-Host "OK: FinancePaperAgent-Prepare registered (runs at each logon)"

# Task 2: Send (every Friday at 09:00)
$sendAction   = New-ScheduledTaskAction -Execute $python -Argument "`"$script`" --send" -WorkingDirectory $workDir
$sendTrigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At "09:00"
$sendSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew -StartWhenAvailable

Register-ScheduledTask `
    -TaskName    "FinancePaperAgent-Send" `
    -Action      $sendAction `
    -Trigger     $sendTrigger `
    -Settings    $sendSettings `
    -RunLevel    Limited `
    -Description "Send cached weekly QIS paper digest every Friday at 09:00" `
    -Force

Write-Host "OK: FinancePaperAgent-Send registered (runs every Friday 09:00)"

# Remove old combined task if it exists
$old = Get-ScheduledTask -TaskName "FinancePaperAgent" -ErrorAction SilentlyContinue
if ($old) {
    Unregister-ScheduledTask -TaskName "FinancePaperAgent" -Confirm:$false
    Write-Host "OK: Old FinancePaperAgent task removed"
}

Write-Host ""
Write-Host "Done. Open taskschd.msc to verify."
