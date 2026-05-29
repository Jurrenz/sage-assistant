#Requires AutoHotkey v2.0
#SingleInstance Force
SetTitleMatchMode(2)
CoordMode("Mouse", "Screen")

if A_Args.Length < 2 {
    MsgBox("Usage: sage_calibrate_position.ahk chemin\vers\settings.json position_name")
    ExitApp(1)
}

settingsPath := A_Args[1]
positionName := A_Args[2]
settingsText := FileRead(settingsPath, "UTF-8")
windowTitle := JsonGetString(settingsText, "window_title_contains", "Sage")

if !WinExist(windowTitle) {
    MsgBox("Fenetre Sage introuvable avec le titre contenant: " windowTitle)
    ExitApp(1)
}

hwnd := WinExist(windowTitle)
winRef := "ahk_id " hwnd
WinActivate(winRef)
WinWaitActive(winRef, , 5)
MsgBox("Place la souris sur `" positionName "` dans Sage, puis clique OK.`nLa position sera enregistree relativement a la fenetre Sage.")

WinGetPos(&wx, &wy, &ww, &wh, winRef)
MouseGetPos(&mx, &my)
rx := mx - wx
ry := my - wy

settingsText := SetJsonNumber(settingsText, positionName "_x", rx)
settingsText := SetJsonNumber(settingsText, positionName "_y", ry)
if FileExist(settingsPath) {
    FileDelete(settingsPath)
}
FileAppend(settingsText, settingsPath, "UTF-8")

MsgBox("Position enregistree: " positionName " x=" rx " y=" ry)

SetJsonNumber(src, key, value) {
    pattern := 's)"' key '"\s*:\s*-?\d+'
    replacement := '"' key '": ' value
    if RegExMatch(src, pattern) {
        return RegExReplace(src, pattern, replacement)
    }
    profilePattern := 's)("sage_profile"\s*:\s*\{)'
    if RegExMatch(src, profilePattern) {
        return RegExReplace(src, profilePattern, '$1`n    "' key '": ' value ',', , 1)
    }
    return src
}

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
