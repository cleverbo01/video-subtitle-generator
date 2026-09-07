Option Explicit

Dim shell, files, scriptDir, pythonExe, appFile, configFile, configText, regex, matches, command, result
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")

scriptDir = files.GetParentFolderName(WScript.ScriptFullName)
appFile = files.BuildPath(scriptDir, "app.py")
configFile = files.BuildPath(scriptDir, "project_config.json")
pythonExe = ""
If files.FileExists(configFile) Then
    configText = files.OpenTextFile(configFile, 1, False, -1).ReadAll
    Set regex = New RegExp
    regex.Pattern = """pythonw_path""\s*:\s*""([^""]+)"""
    regex.IgnoreCase = True
    If regex.Test(configText) Then
        Set matches = regex.Execute(configText)
        pythonExe = Replace(matches(0).SubMatches(0), "\\", "\")
    End If
End If
If Not files.FileExists(pythonExe) Then
    shell.CurrentDirectory = scriptDir
    command = "pyw -3 " & Chr(34) & appFile & Chr(34)
    shell.Run command, 0, False
    WScript.Quit 1
End If

If Not files.FileExists(appFile) Then
    MsgBox "Application file was not found:" & vbCrLf & appFile, vbCritical, "Whisper Subtitle GUI"
    WScript.Quit 1
End If

shell.CurrentDirectory = scriptDir
command = Chr(34) & pythonExe & Chr(34) & " " & Chr(34) & appFile & Chr(34)
result = shell.Run(command, 0, False)

If result <> 0 Then
    MsgBox "Application failed to start. Error code: " & result, vbCritical, "Whisper Subtitle GUI"
End If
