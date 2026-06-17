#Requires AutoHotkey v2.0
#SingleInstance Force
SetTitleMatchMode(2)
CoordMode("Mouse", "Screen")

; AutoHotkey v2 injector for Sage Assistant.
; Hotkeys:
;   Ctrl+Alt+S = stop immediately

global StopRequested := false
global LogEnabled := true
global CaptureEnabled := true
global ControlPath := ""
global LastControlCommand := ""
global UserInputLocked := false

^!s:: {
    RequestStop("hotkey")
}

OnExit(DisableUserInputLock)

if A_Args.Length < 1 {
    MsgBox("Usage: sage_injector.ahk chemin\vers\sage_queue.json")
    ExitApp(1)
}

queuePath := A_Args[1]
if !FileExist(queuePath) {
    MsgBox("File injection introuvable:`n" queuePath)
    ExitApp(1)
}

jsonText := FileRead(queuePath, "UTF-8")
queue := Jxon_Load(&jsonText)
profile := queue["profile"]
lines := queue["lines"]
ControlPath := queue.Has("control_path") ? queue["control_path"] : ""

windowTitle := profile.Has("window_title_contains") ? profile["window_title_contains"] : "Sage 50 : S.Z FASHION"
injectionMode := "real_sage_one_line"
delayMs := profile.Has("delay_ms") ? Integer(profile["delay_ms"]) : 80
afterArticleTabs := profile.Has("after_article_tabs") ? Integer(profile["after_article_tabs"]) : 1
afterDescriptionTabs := profile.Has("after_description_tabs") ? Integer(profile["after_description_tabs"]) : 1
afterQuantityTabs := profile.Has("after_quantity_tabs") ? Integer(profile["after_quantity_tabs"]) : 1
validateKey := profile.Has("validate_key") ? profile["validate_key"] : "Enter"
focusGuard := profile.Has("focus_guard") ? ToBool(profile["focus_guard"]) : true
stepMode := false
logPath := profile.Has("log_path") ? profile["log_path"] : A_ScriptDir "\sage_injection.log"
LogEnabled := profile.Has("log_enabled") ? ToBool(profile["log_enabled"]) : true
CaptureEnabled := profile.Has("capture_before_after") ? ToBool(profile["capture_before_after"]) : true
confirmationMode := profile.Has("confirmation_mode") ? profile["confirmation_mode"] : "simple"
stablePauseMs := profile.Has("stable_pause_ms") ? Integer(profile["stable_pause_ms"]) : 220
if (confirmationMode = "debug") {
    ; Debug adds visible checkpoints. Capture/log checkboxes are still honored.
}

LogLine(logPath, "START queue=" queuePath " lines=" lines.Length)

if (confirmationMode = "debug") {
    MsgBox("Prepare Sage sur une facture brouillon, puis clique OK.`nMode: Injection Sage reelle`n`nStop: Ctrl+Alt+S", "Sage Assistant")
}

if !WinExist(windowTitle) {
    LogLine(logPath, "ERROR Sage window not found: " windowTitle)
    MsgBox("Fenetre Sage introuvable avec le titre contenant: " windowTitle)
    ExitApp(1)
}

WinActivate(windowTitle)
WinWaitActive(windowTitle, , 5)
if focusGuard && !WinActive(windowTitle) {
    LogLine(logPath, "ERROR Sage window not active after activation")
    MsgBox("Sage n'est pas actif. Injection annulee.")
    ExitApp(1)
}

realTarget := FindRealSageOneLineTarget(windowTitle, logPath)
beforePath := CaptureWindow(realTarget["mainHwnd"], logPath, "before")
if CaptureEnabled
    LogLine(logPath, "CAPTURE before=" beforePath)
if (confirmationMode = "debug") {
    MsgBox("Controle avant injection:`nFacture: " realTarget["invoiceTitle"] "`nBouton: &Ajouter`nLignes: " lines.Length "`n`nStop: Ctrl+Alt+S`n`nClique OK pour envoyer les lignes dans Sage.", "Sage Assistant")
} else if (confirmationMode = "simple") {
    MsgBox("Sage pret: " realTarget["invoiceTitle"] "`nLignes: " lines.Length "`n`nClique OK pour injecter.", "Sage Assistant")
}
ActivateSageTarget(realTarget["mainHwnd"], windowTitle)
EnableUserInputLock()
EnsureSageActive(windowTitle, focusGuard, logPath, 1, lines[1]["ref"], realTarget["mainHwnd"])
Click(realTarget["addCenterX"], realTarget["addCenterY"])
StableSleep(delayMs * 2, stablePauseMs)
EnsureSageActive(windowTitle, focusGuard, logPath, 1, lines[1]["ref"], realTarget["mainHwnd"])
afterClickPath := CaptureWindow(realTarget["mainHwnd"], logPath, "after_click")
if CaptureEnabled
    LogLine(logPath, "CAPTURE after_click=" afterClickPath)
activeCell := FocusedControlRect(windowTitle)
LogLine(logPath, "ACTIVE after_add rect=" activeCell.left "," activeCell.top "," activeCell.right "," activeCell.bottom)
focusAtArticle := false

for index, line in lines {
    if StopRequested {
        LogLine(logPath, "STOP before line " index " ref=" line["ref"])
        RequestStop("before_line")
        ExitApp(2)
    }
    EnsureSageActive(windowTitle, focusGuard, logPath, index, line["ref"], realTarget["mainHwnd"])
    LogLine(logPath, "SEND line=" index " ref=" line["ref"] " mode=keyboard_from_sage_focus article_focus=" focusAtArticle)
    SendRealSageLineByKeyboard(line, validateKey, delayMs, windowTitle, focusGuard, logPath, realTarget, index, index < lines.Length, focusAtArticle)
    LogLine(logPath, "OK line=" index " ref=" line["ref"])
    focusAtArticle := index < lines.Length
}

afterPath := CaptureWindow(realTarget["mainHwnd"], logPath, "after")
if CaptureEnabled
    LogLine(logPath, "CAPTURE after=" afterPath)
LogLine(logPath, "DONE lines=" lines.Length)
DisableUserInputLock()
if (confirmationMode = "debug" && CaptureEnabled)
    MsgBox("Injection envoyee.`nVerifie visuellement les lignes dans Sage avant toute autre action.`n`nCapture avant:`n" beforePath "`n`nCapture apres clic:`n" afterClickPath "`n`nCapture apres:`n" afterPath, "Sage Assistant")
else if (confirmationMode = "simple")
    MsgBox("Injection envoyee.`nVerifie visuellement Sage.", "Sage Assistant")
else if (confirmationMode = "direct")
    MsgBox("Injection envoyee.`nVerifie visuellement Sage.", "Sage Assistant")
ExitApp(0)

SendRealSageLineByKeyboard(line, validateKey, delayMs, windowTitle, focusGuard, logPath, realTarget, index, moveToNextLine, focusAtArticle) {
    global CaptureEnabled, stablePauseMs
    if !focusAtArticle {
        Send("+{Tab}")
        StableSleep(delayMs * 2, stablePauseMs)
        Send("+{Tab}")
        StableSleep(delayMs * 2, stablePauseMs)
    }
    EnsureSageActive(windowTitle, focusGuard, logPath, index, line["ref"], realTarget["mainHwnd"])

    SendText(line["article_code"])
    StableSleep(delayMs * 2, stablePauseMs)
    Send("{Tab}")
    StableSleep(delayMs * 8, stablePauseMs)
    EnsureSageActive(windowTitle, focusGuard, logPath, index, line["ref"], realTarget["mainHwnd"])
    afterArticlePath := CaptureWindow(realTarget["mainHwnd"], logPath, "after_article_" index)
    if CaptureEnabled
        LogLine(logPath, "CAPTURE after_article=" afterArticlePath)
    StableSleep(delayMs, stablePauseMs)

    Send("{Up}")
    StableSleep(delayMs * 2, stablePauseMs)
    Send("{Left}")
    StableSleep(delayMs * 2, stablePauseMs)
    Send("{Space}")
    StableSleep(delayMs * 2, stablePauseMs)
    Send("^a")
    StableSleep(delayMs * 2, stablePauseMs)
    SendText(NormalizeSpaces(line["description"]))
    StableSleep(delayMs * 2, stablePauseMs)
    EnsureSageActive(windowTitle, focusGuard, logPath, index, line["ref"], realTarget["mainHwnd"])
    afterDescriptionPath := CaptureWindow(realTarget["mainHwnd"], logPath, "after_description_" index)
    if CaptureEnabled
        LogLine(logPath, "CAPTURE after_description=" afterDescriptionPath)
    StableSleep(delayMs, stablePauseMs)

    Send("{Tab}")
    StableSleep(delayMs * 2, stablePauseMs)
    Send("{Tab}")
    StableSleep(delayMs * 2, stablePauseMs)
    SendText(String(line["quantity"]))
    StableSleep(delayMs * 2, stablePauseMs)
    EnsureSageActive(windowTitle, focusGuard, logPath, index, line["ref"], realTarget["mainHwnd"])
    afterQuantityPath := CaptureWindow(realTarget["mainHwnd"], logPath, "after_quantity_" index)
    if CaptureEnabled
        LogLine(logPath, "CAPTURE after_quantity=" afterQuantityPath)
    StableSleep(delayMs, stablePauseMs)

    Send("{Tab}")
    StableSleep(delayMs * 2, stablePauseMs)
    Send("{Tab}")
    StableSleep(delayMs * 2, stablePauseMs)
    SendText(line["unit_price_ht"])
    StableSleep(delayMs * 2, stablePauseMs)
    EnsureSageActive(windowTitle, focusGuard, logPath, index, line["ref"], realTarget["mainHwnd"])
    afterPricePath := CaptureWindow(realTarget["mainHwnd"], logPath, "after_price_" index)
    if CaptureEnabled
        LogLine(logPath, "CAPTURE after_price=" afterPricePath)
    StableSleep(delayMs, stablePauseMs)

    if moveToNextLine {
        Send("{" validateKey "}")
        StableSleep(delayMs * 3, stablePauseMs)
        Send("{Down}")
        StableSleep(delayMs * 2, stablePauseMs)
        Send("{Left}")
        StableSleep(delayMs, stablePauseMs)
        Send("{Left}")
        StableSleep(delayMs, stablePauseMs)
        Send("{Left}")
        StableSleep(delayMs * 2, stablePauseMs)
    } else {
        Send("{" validateKey "}")
        StableSleep(delayMs, stablePauseMs)
    }
}

FindRealSageOneLineTarget(windowTitle, logPath) {
    mainHwnd := FindSageMainWindow(windowTitle)
    if !mainHwnd {
        LogLine(logPath, "ERROR real Sage main window not found title=" windowTitle)
        MsgBox("Fenetre Sage 50 introuvable.`nTitre attendu: " windowTitle, "Sage Assistant")
        ExitApp(6)
    }
    mainTitle := WinGetTitle("ahk_id " mainHwnd)
    mainClass := WinGetClass("ahk_id " mainHwnd)
    LogLine(logPath, "FOUND Sage main hwnd=" HwndHex(mainHwnd) " class=" mainClass " title=" mainTitle)

    invoiceHwnd := 0
    invoiceTitle := ""
    invoiceCount := 0
    invoiceTitles := []
    for hwnd in WinGetControlsHwnd("ahk_id " mainHwnd) {
        try {
            cls := WinGetClass("ahk_id " hwnd)
            title := WinGetTitle("ahk_id " hwnd)
            if (cls = "CamDialog" && IsWindowVisible(hwnd) && RegExMatch(title, "^(Facture|Nouvelle facture)")) {
                invoiceHwnd := hwnd
                invoiceTitle := title
                invoiceCount += 1
                invoiceTitles.Push(title)
            }
        }
    }
    if invoiceCount != 1 {
        LogLine(logPath, "ERROR invoice window count=" invoiceCount)
        for index, title in invoiceTitles {
            LogLine(logPath, "INVOICE candidate " index "=" title)
        }
        detected := invoiceTitles.Length ? JoinLines(invoiceTitles) : "aucune"
        MsgBox("Facture Sage active introuvable ou ambigue.`nOuvre exactement une fenetre facture.`n`nTitres acceptes: Facture..., Nouvelle facture...`nDetectees:`n" detected, "Sage Assistant")
        ExitApp(7)
    }
    LogLine(logPath, "FOUND invoice hwnd=" HwndHex(invoiceHwnd) " title=" invoiceTitle)

    addButtons := []
    popupCandidates := []
    gridCandidates := []
    for hwnd in WinGetControlsHwnd("ahk_id " invoiceHwnd) {
        try {
            cls := WinGetClass("ahk_id " hwnd)
            text := ControlLabel(hwnd)
            if (cls = "CamPopup" && IsWindowVisible(hwnd)) {
                rect := GetWindowRect(hwnd)
                popupCandidates.Push(HwndHex(hwnd) "|" text "|" rect.left "," rect.top "," (rect.right - rect.left) "," (rect.bottom - rect.top))
            }
            if (cls = "CamPopup" && text = "&Ajouter" && IsWindowVisible(hwnd)) {
                rect := GetWindowRect(hwnd)
                addButtons.Push(Map("hwnd", hwnd, "left", rect.left, "top", rect.top, "right", rect.right, "bottom", rect.bottom))
            }
            if (cls = "CamGrid" && IsWindowVisible(hwnd)) {
                rect := GetWindowRect(hwnd)
                if ((rect.right - rect.left) > 500 && (rect.bottom - rect.top) > 200) {
                    gridCandidates.Push(Map("hwnd", hwnd, "left", rect.left, "top", rect.top, "right", rect.right, "bottom", rect.bottom))
                }
            }
        }
    }
    if addButtons.Length != 1 {
        LogLine(logPath, "ERROR add button count=" addButtons.Length)
        for index, candidate in popupCandidates {
            LogLine(logPath, "CAMPOPUP candidate " index "=" candidate)
        }
        MsgBox("Bouton Sage '&Ajouter' introuvable ou ambigu dans la facture.", "Sage Assistant")
        ExitApp(8)
    }
    if gridCandidates.Length != 1 {
        LogLine(logPath, "ERROR invoice grid count=" gridCandidates.Length)
        MsgBox("Grille lignes Sage introuvable ou ambigue dans la facture.", "Sage Assistant")
        ExitApp(9)
    }
    add := addButtons[1]
    grid := gridCandidates[1]
    centerX := Floor((add["left"] + add["right"]) / 2)
    centerY := Floor((add["top"] + add["bottom"]) / 2)
    LogLine(logPath, "FOUND add hwnd=" HwndHex(add["hwnd"]) " rect=" add["left"] "," add["top"] "," add["right"] "," add["bottom"] " center=" centerX "," centerY)
    LogLine(logPath, "FOUND grid hwnd=" HwndHex(grid["hwnd"]) " rect=" grid["left"] "," grid["top"] "," grid["right"] "," grid["bottom"])
    return Map(
        "mainHwnd", mainHwnd,
        "invoiceHwnd", invoiceHwnd,
        "invoiceTitle", invoiceTitle,
        "addHwnd", add["hwnd"],
        "addCenterX", centerX,
        "addCenterY", centerY,
        "gridHwnd", grid["hwnd"],
        "gridLeft", grid["left"],
        "gridTop", grid["top"],
        "gridRight", grid["right"],
        "gridBottom", grid["bottom"]
    )
}

FocusedControlRect(windowTitle) {
    try {
        focusedControl := ControlGetFocus(windowTitle)
        focusedHwnd := ControlGetHwnd(focusedControl, windowTitle)
        rect := GetWindowRect(focusedHwnd)
        if (rect.right - rect.left) > 0 && (rect.bottom - rect.top) > 0 {
            return rect
        }
    }
    WinGetPos(&wx, &wy, &ww, &wh, windowTitle)
    return {left: wx + 520, top: wy + 570, right: wx + 625, bottom: wy + 595}
}

ControlLabel(hwnd) {
    text := ""
    try {
        text := ControlGetText("ahk_id " hwnd)
    }
    if text {
        return text
    }
    try {
        return WinGetTitle("ahk_id " hwnd)
    }
    return ""
}

FindSageMainWindow(windowTitle) {
    for hwnd in WinGetList(windowTitle) {
        try {
            if (WinGetClass("ahk_id " hwnd) = "CamMainFrame" && WinGetProcessName("ahk_id " hwnd) = "WGC.exe") {
                return hwnd
            }
        }
    }
    return 0
}

IsWindowVisible(hwnd) {
    return DllCall("user32\IsWindowVisible", "ptr", hwnd, "int") != 0
}

GetWindowRect(hwnd) {
    buf := Buffer(16, 0)
    if !DllCall("user32\GetWindowRect", "ptr", hwnd, "ptr", buf, "int") {
        throw Error("GetWindowRect failed for " HwndHex(hwnd))
    }
    return {
        left: NumGet(buf, 0, "int"),
        top: NumGet(buf, 4, "int"),
        right: NumGet(buf, 8, "int"),
        bottom: NumGet(buf, 12, "int"),
    }
}

CaptureWindow(hwnd, logPath, label) {
    global CaptureEnabled
    if !CaptureEnabled {
        return ""
    }
    SplitPath(logPath, , &dir)
    if !dir {
        dir := A_ScriptDir
    }
    captureDir := dir "\captures"
    if !DirExist(captureDir) {
        DirCreate(captureDir)
    }
    path := captureDir "\sage_" label "_" FormatTime(, "yyyyMMdd_HHmmss") ".png"
    rect := GetWindowRect(hwnd)
    ps := "$p='" EscapePowerShell(path) "';$x=" rect.left ";$y=" rect.top ";$w=" (rect.right - rect.left) ";$h=" (rect.bottom - rect.top) ";Add-Type -AssemblyName System.Windows.Forms;Add-Type -AssemblyName System.Drawing;$b=New-Object Drawing.Bitmap $w,$h;$g=[Drawing.Graphics]::FromImage($b);$g.CopyFromScreen($x,$y,0,0,$b.Size);$b.Save($p,[Drawing.Imaging.ImageFormat]::Png);$g.Dispose();$b.Dispose()"
    RunWait("powershell -NoProfile -ExecutionPolicy Bypass -Command " QuoteArg(ps), , "Hide")
    return path
}

StableSleep(dynamicMs, stableMs) {
    global StopRequested
    duration := Max(Integer(dynamicMs), Integer(stableMs))
    elapsed := 0
    while elapsed < duration {
        CheckExternalControl()
        if StopRequested {
            RequestStop("sleep")
            ExitApp(2)
        }
        chunk := Min(100, duration - elapsed)
        Sleep(chunk)
        elapsed += chunk
    }
    CheckExternalControl()
    if StopRequested {
        RequestStop("sleep_end")
        ExitApp(2)
    }
}

NormalizeSpaces(text) {
    return RegExReplace(Trim(String(text)), "\s+", " ")
}

EscapePowerShell(text) {
    return StrReplace(text, "'", "''")
}

QuoteArg(text) {
    return '"' StrReplace(text, '"', '\"') '"'
}

HwndHex(hwnd) {
    return Format("0x{:X}", Integer(hwnd))
}

JoinLines(items) {
    text := ""
    for index, item in items {
        text .= (index = 1 ? "" : "`n") item
    }
    return text
}

LogLine(logPath, "DONE lines=" lines.Length)
ToolTip("Injection Sage terminee")
SetTimer(() => ToolTip(), -1500)
ExitApp(0)

CheckExternalControl() {
    global ControlPath, LastControlCommand, StopRequested
    if !ControlPath || !FileExist(ControlPath) {
        return
    }
    try {
        command := StrLower(Trim(FileRead(ControlPath, "UTF-8")))
    } catch {
        return
    }
    if (command = LastControlCommand) {
        return
    }
    LastControlCommand := command
    if (command = "stop") {
        RequestStop("control_file")
    }
}

RequestStop(reason := "") {
    global StopRequested, logPath
    StopRequested := true
    DisableUserInputLock()
    if IsSet(logPath) && logPath {
        LogLine(logPath, "STOP requested reason=" reason)
    }
    ToolTip("Injection Sage stoppee")
    SetTimer(() => ToolTip(), -1200)
}

EnableUserInputLock() {
    global UserInputLocked
    if UserInputLocked {
        return
    }
    UserInputLocked := true
    for key in UserInputLockKeys() {
        try Hotkey("*" key, SwallowUserKey, "On")
    }
    for key in UserInputLockMouseKeys() {
        try Hotkey("*" key, GuardUserMouse, "On")
    }
}

DisableUserInputLock(*) {
    global UserInputLocked
    if !UserInputLocked {
        return
    }
    for key in UserInputLockKeys() {
        try Hotkey("*" key, "Off")
    }
    for key in UserInputLockMouseKeys() {
        try Hotkey("*" key, "Off")
    }
    UserInputLocked := false
}

SwallowUserKey(*) {
    key := RegExReplace(A_ThisHotkey, "^[*~$]+")
    if (StrLower(key) = "s" && GetKeyState("Ctrl", "P") && GetKeyState("Alt", "P")) {
        RequestStop("locked_hotkey")
    }
}

GuardUserMouse(*) {
    if !MouseOverInjectionControl() {
        return
    }
    hotkeyName := A_ThisHotkey
    key := RegExReplace(hotkeyName, "^[*~$]+")
    try Hotkey(hotkeyName, "Off")
    try Send("{" key "}")
    try Hotkey(hotkeyName, GuardUserMouse, "On")
}

MouseOverInjectionControl() {
    try {
        MouseGetPos(, , &hwnd)
        if !hwnd {
            return false
        }
        title := WinGetTitle("ahk_id " hwnd)
        if (InStr(title, "Injection Sage en cours") > 0) {
            return true
        }
        rootHwnd := DllCall("GetAncestor", "ptr", hwnd, "uint", 2, "ptr")
        if !rootHwnd {
            return false
        }
        rootTitle := WinGetTitle("ahk_id " rootHwnd)
        return InStr(rootTitle, "Injection Sage en cours") > 0
    } catch {
        return false
    }
}

UserInputLockKeys() {
    return [
        "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m",
        "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z",
        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
        "Space", "Enter", "Tab", "Backspace", "Delete", "Insert", "Home", "End",
        "PgUp", "PgDn", "Up", "Down", "Left", "Right", "Escape",
        "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
        "Numpad0", "Numpad1", "Numpad2", "Numpad3", "Numpad4", "Numpad5", "Numpad6", "Numpad7", "Numpad8", "Numpad9",
        "NumpadDot", "NumpadDiv", "NumpadMult", "NumpadAdd", "NumpadSub", "NumpadEnter",
        "-", "=", "[", "]", "\", ";", "'", ",", ".", "/", "``"
    ]
}

UserInputLockMouseKeys() {
    return ["LButton", "RButton", "MButton", "XButton1", "XButton2", "WheelUp", "WheelDown", "WheelLeft", "WheelRight"]
}

ActivateSageTarget(mainHwnd, windowTitle) {
    if mainHwnd {
        WinActivate("ahk_id " mainHwnd)
        WinWaitActive("ahk_id " mainHwnd, , 2)
    } else {
        WinActivate(windowTitle)
        WinWaitActive(windowTitle, , 2)
    }
}

EnsureSageActive(windowTitle, focusGuard, logPath, index, ref, mainHwnd := 0) {
    if !focusGuard {
        return
    }
    if IsSageActive(windowTitle, mainHwnd) {
        return
    }
    ActivateSageTarget(mainHwnd, windowTitle)
    StableSleep(100, 100)
    if !IsSageActive(windowTitle, mainHwnd) {
        activeTitle := ""
        activeClass := ""
        try {
            activeTitle := WinGetTitle("A")
            activeClass := WinGetClass("A")
        }
        LogLine(logPath, "ERROR focus lost line=" index " ref=" ref " active_title=" activeTitle " active_class=" activeClass)
        MsgBox("Sage n'est plus actif. Injection stoppee a la ligne " index " (" ref ").`n`nFenetre active: " activeTitle, "Sage Assistant")
        ExitApp(3)
    }
}

IsSageActive(windowTitle, mainHwnd := 0) {
    if WinActive(windowTitle) {
        return true
    }
    activeHwnd := WinExist("A")
    if !activeHwnd || !mainHwnd {
        return false
    }
    activeRoot := DllCall("user32\GetAncestor", "ptr", activeHwnd, "uint", 2, "ptr")
    mainRoot := DllCall("user32\GetAncestor", "ptr", mainHwnd, "uint", 2, "ptr")
    return activeHwnd = mainHwnd || activeRoot = mainRoot
}

LogLine(logPath, message) {
    global LogEnabled
    if !LogEnabled {
        return
    }
    SplitPath(logPath, , &dir)
    if dir && !DirExist(dir) {
        DirCreate(dir)
    }
    stamp := FormatTime(, "yyyy-MM-dd HH:mm:ss")
    FileAppend(stamp " | " message "`n", logPath, "UTF-8")
}

ToBool(value) {
    if IsNumber(value) {
        return value != 0
    }
    text := StrLower(String(value))
    return text = "true" || text = "1" || text = "yes" || text = "oui"
}

; Small reader for the Sage Assistant queue JSON. It intentionally supports only
; the predictable queue shape written by app/injection.py.
Jxon_Load(&src, args*) {
    profileText := JsonExtractObject(src, "profile")
    linesText := JsonExtractArray(src, "lines")

    profile := Map()
    profile["injection_mode"] := JsonGetString(profileText, "injection_mode", "real_sage_one_line")
    profile["window_title_contains"] := JsonGetString(profileText, "window_title_contains", "Sage 50 : S.Z FASHION")
    profile["start_position"] := JsonGetString(profileText, "start_position", "article_code")
    profile["delay_ms"] := JsonGetNumber(profileText, "delay_ms", 80)
    profile["after_article_tabs"] := JsonGetNumber(profileText, "after_article_tabs", 1)
    profile["after_description_tabs"] := JsonGetNumber(profileText, "after_description_tabs", 1)
    profile["after_quantity_tabs"] := JsonGetNumber(profileText, "after_quantity_tabs", 1)
    profile["validate_key"] := JsonGetString(profileText, "validate_key", "Enter")
    profile["focus_guard"] := JsonGetBool(profileText, "focus_guard", true)
    profile["step_mode"] := JsonGetBool(profileText, "step_mode", false)
    profile["log_enabled"] := JsonGetBool(profileText, "log_enabled", true)
    profile["capture_before_after"] := JsonGetBool(profileText, "capture_before_after", true)
    profile["confirmation_mode"] := JsonGetString(profileText, "confirmation_mode", "simple")
    profile["stable_pause_ms"] := JsonGetNumber(profileText, "stable_pause_ms", 220)
    profile["log_path"] := JsonGetString(profileText, "log_path", A_ScriptDir "\sage_injection.log")
    profile["new_line_x"] := JsonGetNumber(profileText, "new_line_x", 0)
    profile["new_line_y"] := JsonGetNumber(profileText, "new_line_y", 0)
    profile["article_cell_x"] := JsonGetNumber(profileText, "article_cell_x", 0)
    profile["article_cell_y"] := JsonGetNumber(profileText, "article_cell_y", 0)

    lines := []
    pos := 1
    while (pos := RegExMatch(linesText, "s)\{(.*?)\}", &match, pos)) {
        itemText := match[1]
        line := Map()
        line["article_code"] := JsonGetString(itemText, "article_code")
        line["description"] := JsonGetString(itemText, "description")
        line["quantity"] := JsonGetNumber(itemText, "quantity", 0)
        line["unit_price_ht"] := JsonGetString(itemText, "unit_price_ht")
        line["ref"] := JsonGetString(itemText, "ref")
        lines.Push(line)
        pos += StrLen(match[0])
    }

    if lines.Length = 0 {
        throw Error("Aucune ligne trouvee dans la file injection.")
    }

    return Map("profile", profile, "lines", lines, "control_path", JsonGetString(src, "control_path", ""))
}

JsonExtractObject(src, key) {
    pattern := 's)"' key '"\s*:\s*\{(.*?)\}'
    if !RegExMatch(src, pattern, &match) {
        throw Error("Bloc JSON introuvable: " key)
    }
    return match[1]
}

JsonExtractArray(src, key) {
    pattern := 's)"' key '"\s*:\s*\[(.*)\]\s*\}'
    if !RegExMatch(src, pattern, &match) {
        throw Error("Tableau JSON introuvable: " key)
    }
    return match[1]
}

JsonGetString(src, key, default := "") {
    pattern := 's)"' key '"\s*:\s*"((?:\\.|[^"\\])*)"'
    if RegExMatch(src, pattern, &match) {
        return JsonUnescape(match[1])
    }
    return default
}

JsonGetNumber(src, key, default := 0) {
    pattern := 's)"' key '"\s*:\s*(-?\d+(?:\.\d+)?)'
    if RegExMatch(src, pattern, &match) {
        return InStr(match[1], ".") ? Float(match[1]) : Integer(match[1])
    }
    return default
}

JsonGetBool(src, key, default := false) {
    pattern := 's)"' key '"\s*:\s*(true|false)'
    if RegExMatch(src, pattern, &match) {
        return match[1] = "true"
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
