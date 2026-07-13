# salonboard_sync.py を1時間ごとに自動実行するタスクをWindowsタスクスケジューラに登録する。
# 使い方: このフォルダで PowerShell を開き、以下を実行する。
#   powershell -ExecutionPolicy Bypass -File .\register_task.ps1

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = (Get-Command python).Source
$TaskName = "AtelierSalonboardSync"
$LogFile = "$ScriptDir\debug\task_log.txt"

New-Item -ItemType Directory -Path "$ScriptDir\debug" -Force | Out-Null

# 実行結果(標準出力・標準エラー)をログファイルに追記する。
# タスクスケジューラ実行時は画面に何も表示されないため、動作確認はこのログか
# debug フォルダ内のスクリーンショット/HTMLで行う。
$CmdArgument = "/c `"`"$PythonExe`" `"$ScriptDir\salonboard_sync.py`" >> `"$LogFile`" 2>&1`""
$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $CmdArgument -WorkingDirectory $ScriptDir
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration ([TimeSpan]::MaxValue)
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "salonboardの受付締切設定を1時間ごとに確認・維持する"

Write-Host "タスク '$TaskName' を登録しました。タスクスケジューラのアプリで確認できます。"
