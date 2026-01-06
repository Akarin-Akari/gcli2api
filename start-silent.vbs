' gcli2api Silent Startup Script with Logging
' 静默启动 gcli2api 服务，带日志输出
' 作者：幽浮喵 (浮浮酱)
' 日期：2025-12-22

Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

strPath = FSO.GetParentFolderName(WScript.ScriptFullName)
strLogDir = strPath & "\logs"
strDate = Year(Now) & "-" & Right("0" & Month(Now), 2) & "-" & Right("0" & Day(Now), 2)
strGcliLog = strLogDir & "\gcli2api_" & strDate & ".log"
strNgrokLog = strLogDir & "\ngrok_" & strDate & ".log"

' 创建日志目录
If Not FSO.FolderExists(strLogDir) Then
    FSO.CreateFolder(strLogDir)
End If

' 静默启动 gcli2api 服务（带日志输出）
strGcliCmd = "cmd /c cd /d """ & strPath & """ && call .venv\Scripts\activate.bat && python web.py >> """ & strGcliLog & """ 2>&1"
WshShell.Run strGcliCmd, 0, False

' 等待 3 秒让服务启动
WScript.Sleep 3000

' 静默启动 ngrok 隧道（带日志输出）
strNgrokCmd = "cmd /c cd /d """ & strPath & "\..\ngrok_temp"" && ngrok.exe http 7861 --log=stdout >> """ & strNgrokLog & """ 2>&1"
WshShell.Run strNgrokCmd, 0, False
