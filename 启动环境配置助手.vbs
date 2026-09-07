Option Explicit

Dim shell, files, scriptDir, pythonExe, setupFile, configFile, configText, regex, matches, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")

scriptDir = files.GetParentFolderName(WScript.ScriptFullName)
setupFile = files.BuildPath(scriptDir, "environment_setup.py")
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
    command = "pyw -3 " & Chr(34) & setupFile & Chr(34)
    shell.Run command, 0, False
    WScript.Quit 1
End If

shell.CurrentDirectory = scriptDir
command = Chr(34) & pythonExe & Chr(34) & " " & Chr(34) & setupFile & Chr(34)
shell.Run command, 0, False
