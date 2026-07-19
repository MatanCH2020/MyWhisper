' Launch MyWhisper silently (no console window) as a SINGLE process.
'
' A venv's pythonw.exe is only a thin launcher that spawns the base interpreter,
' which shows up as TWO processes for one app. To run just one process we launch
' the base interpreter directly and set __PYVENV_LAUNCHER__ so it still adopts
' the venv (same sys.prefix / site-packages, so CUDA + wordfreq keep working).
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
rootDir = fso.GetParentFolderName(scriptDir)
venvPythonw = fso.BuildPath(rootDir, ".venv\Scripts\pythonw.exe")
mainPy = fso.BuildPath(rootDir, "app\main.py")

' Resolve the base interpreter from the venv's pyvenv.cfg (home = <dir>).
basePythonw = venvPythonw  ' fallback: the venv launcher (two processes) if cfg missing
cfgPath = fso.BuildPath(rootDir, ".venv\pyvenv.cfg")
If fso.FileExists(cfgPath) Then
    Set f = fso.OpenTextFile(cfgPath, 1)
    Do Until f.AtEndOfStream
        line = f.ReadLine
        If LCase(Left(LTrim(line), 4)) = "home" Then
            pos = InStr(line, "=")
            If pos > 0 Then
                homeDir = Trim(Mid(line, pos + 1))
                candidate = fso.BuildPath(homeDir, "pythonw.exe")
                If fso.FileExists(candidate) Then basePythonw = candidate
            End If
        End If
    Loop
    f.Close
End If

Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = rootDir
shell.Environment("PROCESS")("__PYVENV_LAUNCHER__") = venvPythonw
shell.Run """" & basePythonw & """ """ & mainPy & """", 0, False
