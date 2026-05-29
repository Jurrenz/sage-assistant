#Requires AutoHotkey v2.0
#SingleInstance Force
SetTitleMatchMode(2)
CoordMode("Mouse", "Screen")

if A_Args.Length < 1 {
    MsgBox("Usage: sage_diagnostics.ahk chemin\vers\settings.json")
    ExitApp(1)
}

settingsPath := A_Args[1]
settingsText := FileRead(settingsPath, "UTF-8")
windowTitle := JsonGetString(settingsText, "window_title_contains", "Sage")
diagnosticsPath := JsonGetString(settingsText, "diagnostics_path", A_ScriptDir "\..\data\sage_window_diagnostics.txt")

if !WinExist(windowTitle) {
    MsgBox("Fenetre Sage introuvable avec le titre contenant: " windowTitle)
    ExitApp(1)
}

hwnd := WinExist(windowTitle)
winRef := "ahk_id " hwnd
WinActivate(winRef)
WinWaitActive(winRef, , 5)

WinGetPos(&wx, &wy, &ww, &wh, winRef)
MouseGetPos(&mx, &my, &mouseWin, &mouseControl)
activeClass := WinGetClass(winRef)
activeTitle := WinGetTitle(winRef)
processName := WinGetProcessName(winRef)
processPath := WinGetProcessPath(winRef)
try {
    controlName := ControlGetFocus(winRef)
} catch as err {
    controlName := "ERREUR: " err.Message
}
try {
    controls := WinGetControls(winRef)
} catch as err {
    controls := ["ERREUR: " err.Message]
}

SplitPath(diagnosticsPath, , &dir)
if dir && !DirExist(dir) {
    DirCreate(dir)
}

text := ""
text .= "Sage Assistant diagnostics`n"
text .= "Generated: " FormatTime(, "yyyy-MM-dd HH:mm:ss") "`n"
text .= "Window title: " activeTitle "`n"
text .= "Window class: " activeClass "`n"
text .= "Process name: " processName "`n"
text .= "Process path: " processPath "`n"
text .= "HWND: " hwnd "`n"
text .= "Window rect: x=" wx " y=" wy " w=" ww " h=" wh "`n"
text .= "Mouse screen: x=" mx " y=" my "`n"
text .= "Mouse relative: x=" (mx - wx) " y=" (my - wy) "`n"
text .= "Mouse control: " mouseControl "`n"
text .= "Focused control: " controlName "`n"
text .= "`nControls:`n"
for index, ctrl in controls {
    text .= index ". " ctrl "`n"
}

if FileExist(diagnosticsPath) {
    FileDelete(diagnosticsPath)
}
FileAppend(text, diagnosticsPath, "UTF-8")
MsgBox("Diagnostic Sage ecrit:`n" diagnosticsPath)

JsonGetString(src, key, default := "") {
    pattern := 's)"' key '"\s*:\s*"((?:\\.|[^"\\])*)"'
    if RegExMatch(src, pattern, &match) {
        return JsonUnescape(match[1])
    }
    return default
}

JsonUnescape(text) {
    text := StrReplace(text, '\"', '"')
    text := StrReplace(text, "\\", "\")
    text := StrReplace(text, "\/", "/")
    text := StrReplace(text, "\n", "`n")
    text := StrReplace(text, "\r", "`r")
    text := StrReplace(text, "\t", "`t")
    return text
}
