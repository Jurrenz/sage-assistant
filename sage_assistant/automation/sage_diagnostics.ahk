#Requires AutoHotkey v2.0
#SingleInstance Force
SetTitleMatchMode(2)
CoordMode("Mouse", "Screen")

if A_Args.Length < 1 {
    MsgBox("Usage: sage_diagnostics.ahk chemin\vers\settings.json")
    ExitApp(1)
}

settingsPath := A_Args[1]
settingsPath := ResolveInputPath(settingsPath)
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
try {
    controlHwnds := WinGetControlsHwnd(winRef)
} catch as err {
    controlHwnds := []
    controlHwndError := err.Message
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
if IsSet(controlHwndError) {
    text .= "Controls HWND error: " controlHwndError "`n"
}
text .= "`nControls summary:`n"
for index, ctrl in controls {
    text .= index ". " ctrl "`n"
}
text .= "`nDetailed controls:`n"
text .= "Index`tDepth`tHWND`tParent`tClass`tText`tTitle`tVisible`tEnabled`tRect`tRelativeToMain`tParentClass`tParentText`n"
for index, ctrlHwnd in controlHwnds {
    text .= DescribeControl(index, ctrlHwnd, hwnd, wx, wy) "`n"
}

text .= "`nVisible CamPopup candidates:`n"
for index, ctrlHwnd in controlHwnds {
    if SafeClass(ctrlHwnd) = "CamPopup" && IsWindowVisible(ctrlHwnd) {
        text .= DescribeControl(index, ctrlHwnd, hwnd, wx, wy) "`n"
    }
}

text .= "`nVisible invoice dialogs:`n"
for index, ctrlHwnd in controlHwnds {
    if SafeClass(ctrlHwnd) = "CamDialog" && IsWindowVisible(ctrlHwnd) && RegExMatch(SafeTitle(ctrlHwnd), "^(Facture|Nouvelle facture)") {
        text .= DescribeControl(index, ctrlHwnd, hwnd, wx, wy) "`n"
    }
}

if FileExist(diagnosticsPath) {
    FileDelete(diagnosticsPath)
}
FileAppend(text, diagnosticsPath, "UTF-8")
MsgBox("Diagnostic Sage ecrit:`n" diagnosticsPath)

ResolveInputPath(path) {
    if FileExist(path) {
        return path
    }
    scriptRelative := A_ScriptDir "\.." "\" path
    if FileExist(scriptRelative) {
        return scriptRelative
    }
    workRelative := A_WorkingDir "\" path
    if FileExist(workRelative) {
        return workRelative
    }
    return path
}

DescribeControl(index, ctrlHwnd, mainHwnd, mainX, mainY) {
    rect := GetWindowRect(ctrlHwnd)
    parent := GetParent(ctrlHwnd)
    return index
        . "`t" ControlDepth(ctrlHwnd, mainHwnd)
        . "`t" HwndHex(ctrlHwnd)
        . "`t" HwndHex(parent)
        . "`t" CleanField(SafeClass(ctrlHwnd))
        . "`t" CleanField(SafeText(ctrlHwnd))
        . "`t" CleanField(SafeTitle(ctrlHwnd))
        . "`t" IsWindowVisible(ctrlHwnd)
        . "`t" IsWindowEnabled(ctrlHwnd)
        . "`t" rect.left "," rect.top "," (rect.right - rect.left) "," (rect.bottom - rect.top)
        . "`t" (rect.left - mainX) "," (rect.top - mainY)
        . "`t" CleanField(SafeClass(parent))
        . "`t" CleanField(SafeText(parent))
}

ControlDepth(hwnd, mainHwnd) {
    depth := 0
    current := hwnd
    while current && current != mainHwnd && depth < 20 {
        current := GetParent(current)
        depth += 1
    }
    return depth
}

SafeClass(hwnd) {
    if !hwnd {
        return ""
    }
    try {
        return WinGetClass("ahk_id " hwnd)
    } catch {
        return ""
    }
}

SafeTitle(hwnd) {
    if !hwnd {
        return ""
    }
    try {
        return WinGetTitle("ahk_id " hwnd)
    } catch {
        return ""
    }
}

SafeText(hwnd) {
    if !hwnd {
        return ""
    }
    try {
        return ControlGetText("ahk_id " hwnd)
    } catch {
        return ""
    }
}

GetParent(hwnd) {
    return DllCall("user32\GetParent", "ptr", hwnd, "ptr")
}

IsWindowVisible(hwnd) {
    return DllCall("user32\IsWindowVisible", "ptr", hwnd, "int") != 0
}

IsWindowEnabled(hwnd) {
    return DllCall("user32\IsWindowEnabled", "ptr", hwnd, "int") != 0
}

GetWindowRect(hwnd) {
    buf := Buffer(16, 0)
    if !DllCall("user32\GetWindowRect", "ptr", hwnd, "ptr", buf, "int") {
        return {left: 0, top: 0, right: 0, bottom: 0}
    }
    return {
        left: NumGet(buf, 0, "int"),
        top: NumGet(buf, 4, "int"),
        right: NumGet(buf, 8, "int"),
        bottom: NumGet(buf, 12, "int"),
    }
}

HwndHex(hwnd) {
    return hwnd ? Format("0x{:X}", Integer(hwnd)) : ""
}

CleanField(value) {
    value := String(value)
    value := StrReplace(value, "`r", " ")
    value := StrReplace(value, "`n", " ")
    value := StrReplace(value, "`t", " ")
    return Trim(value)
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
