#Requires AutoHotkey v2.0
#SingleInstance Force
SetTitleMatchMode(2)

; AutoHotkey v2 injector for Sage Assistant.
; Hotkeys:
;   Ctrl+Alt+P = pause/resume
;   Ctrl+Alt+S = stop immediately
;   Ctrl+Alt+N = next line in step mode

global StopRequested := false
global Paused := false
global NextRequested := false

^!p:: {
    global Paused
    Paused := !Paused
    ToolTip(Paused ? "Injection Sage en pause" : "Injection Sage reprise")
    SetTimer(() => ToolTip(), -1200)
}

^!s:: {
    global StopRequested
    StopRequested := true
    ToolTip("Injection Sage stoppee")
    SetTimer(() => ToolTip(), -1200)
}

^!n:: {
    global NextRequested
    NextRequested := true
    ToolTip("Ligne suivante")
    SetTimer(() => ToolTip(), -700)
}

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

windowTitle := profile.Has("window_title_contains") ? profile["window_title_contains"] : "Sage"
injectionMode := profile.Has("injection_mode") ? profile["injection_mode"] : "keyboard_only"
delayMs := profile.Has("delay_ms") ? Integer(profile["delay_ms"]) : 80
afterArticleTabs := profile.Has("after_article_tabs") ? Integer(profile["after_article_tabs"]) : 1
afterDescriptionTabs := profile.Has("after_description_tabs") ? Integer(profile["after_description_tabs"]) : 1
afterQuantityTabs := profile.Has("after_quantity_tabs") ? Integer(profile["after_quantity_tabs"]) : 1
validateKey := profile.Has("validate_key") ? profile["validate_key"] : "Enter"
focusGuard := profile.Has("focus_guard") ? ToBool(profile["focus_guard"]) : true
stepMode := profile.Has("step_mode") ? ToBool(profile["step_mode"]) : true
logPath := profile.Has("log_path") ? profile["log_path"] : A_ScriptDir "\sage_injection.log"
newLineX := profile.Has("new_line_x") ? Integer(profile["new_line_x"]) : 0
newLineY := profile.Has("new_line_y") ? Integer(profile["new_line_y"]) : 0
articleCellX := profile.Has("article_cell_x") ? Integer(profile["article_cell_x"]) : 0
articleCellY := profile.Has("article_cell_y") ? Integer(profile["article_cell_y"]) : 0

LogLine(logPath, "START queue=" queuePath " lines=" lines.Length)

if (injectionMode = "real_sage_one_line" && lines.Length != 1) {
    LogLine(logPath, "ERROR real_sage_one_line requires exactly one line, got=" lines.Length)
    MsgBox("Le mode Sage reel impose exactement 1 ligne. Injection annulee.", "Sage Assistant")
    ExitApp(5)
}

MsgBox("Prepare Sage sur une facture brouillon, puis clique OK.`nMode: " injectionMode "`n`nPause: Ctrl+Alt+P`nStop: Ctrl+Alt+S`nLigne suivante: Ctrl+Alt+N", "Sage Assistant")

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

if (injectionMode = "real_sage_one_line") {
    line := lines[1]
    realTarget := FindRealSageOneLineTarget(windowTitle, logPath)
    beforePath := CaptureWindow(realTarget["mainHwnd"], logPath, "before")
    LogLine(logPath, "CAPTURE before=" beforePath)
    MsgBox("Controle avant injection:`nFacture: " realTarget["invoiceTitle"] "`nBouton: &Ajouter`nLigne: " line["ref"] "`n`nClique OK pour envoyer UNE ligne dans Sage.", "Sage Assistant")
    EnsureSageActive(windowTitle, focusGuard, logPath, 1, line["ref"])
    Click(realTarget["addCenterX"], realTarget["addCenterY"])
    Sleep(delayMs * 2)
    EnsureSageActive(windowTitle, focusGuard, logPath, 1, line["ref"])
    LogLine(logPath, "SEND line=1 ref=" line["ref"])
    SendInvoiceLine(line, afterArticleTabs, afterDescriptionTabs, afterQuantityTabs, validateKey, delayMs, windowTitle, focusGuard, logPath, 1)
    afterPath := CaptureWindow(realTarget["mainHwnd"], logPath, "after")
    LogLine(logPath, "CAPTURE after=" afterPath)
    LogLine(logPath, "OK line=1 ref=" line["ref"])
    LogLine(logPath, "DONE lines=1")
    MsgBox("Injection envoyee.`nVerifie visuellement la ligne dans Sage avant toute autre action.`n`nCapture avant:`n" beforePath "`n`nCapture apres:`n" afterPath, "Sage Assistant")
    ExitApp(0)
}

for index, line in lines {
    if stepMode {
        WaitNextLine(index, line["ref"], logPath)
    }
    WaitIfPaused()
    if StopRequested {
        LogLine(logPath, "STOP before line " index " ref=" line["ref"])
        ExitApp(2)
    }
    EnsureSageActive(windowTitle, focusGuard, logPath, index, line["ref"])
    LogLine(logPath, "SEND line=" index " ref=" line["ref"])

    if (injectionMode = "calibrated_clicks") {
        PrepareCalibratedLine(windowTitle, newLineX, newLineY, articleCellX, articleCellY, delayMs, focusGuard, logPath, index, line["ref"])
    } else if (injectionMode = "control_based") {
        LogLine(logPath, "INFO control_based not implemented, using keyboard_only line=" index " ref=" line["ref"])
    }

    SendInvoiceLine(line, afterArticleTabs, afterDescriptionTabs, afterQuantityTabs, validateKey, delayMs, windowTitle, focusGuard, logPath, index)
    LogLine(logPath, "OK line=" index " ref=" line["ref"])
}

SendInvoiceLine(line, afterArticleTabs, afterDescriptionTabs, afterQuantityTabs, validateKey, delayMs, windowTitle, focusGuard, logPath, index) {
    SendText(line["article_code"])
    Sleep(delayMs)
    EnsureSageActive(windowTitle, focusGuard, logPath, index, line["ref"])
    SendTabs(afterArticleTabs, delayMs)

    SendText(line["description"])
    Sleep(delayMs)
    EnsureSageActive(windowTitle, focusGuard, logPath, index, line["ref"])
    SendTabs(afterDescriptionTabs, delayMs)

    SendText(String(line["quantity"]))
    Sleep(delayMs)
    EnsureSageActive(windowTitle, focusGuard, logPath, index, line["ref"])
    SendTabs(afterQuantityTabs, delayMs)

    SendText(line["unit_price_ht"])
    Sleep(delayMs)
    EnsureSageActive(windowTitle, focusGuard, logPath, index, line["ref"])
    Send("{" validateKey "}")
    Sleep(delayMs)
}

PrepareCalibratedLine(windowTitle, newLineX, newLineY, articleCellX, articleCellY, delayMs, focusGuard, logPath, index, ref) {
    if !newLineX || !newLineY || !articleCellX || !articleCellY {
        LogLine(logPath, "ERROR missing calibrated positions line=" index " ref=" ref)
        MsgBox("Profil calibrated_clicks incomplet. Enregistre les positions nouvelle ligne et colonne article.")
        ExitApp(4)
    }
    WinGetPos(&wx, &wy, &ww, &wh, windowTitle)
    EnsureSageActive(windowTitle, focusGuard, logPath, index, ref)
    Click(wx + newLineX, wy + newLineY)
    Sleep(delayMs)
    EnsureSageActive(windowTitle, focusGuard, logPath, index, ref)
    Click(wx + articleCellX, wy + articleCellY)
    Sleep(delayMs)
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
    for hwnd in WinGetControlsHwnd("ahk_id " mainHwnd) {
        try {
            cls := WinGetClass("ahk_id " hwnd)
            title := WinGetTitle("ahk_id " hwnd)
            if (cls = "CamDialog" && IsWindowVisible(hwnd) && RegExMatch(title, "^Facture")) {
                invoiceHwnd := hwnd
                invoiceTitle := title
                invoiceCount += 1
            }
        }
    }
    if invoiceCount != 1 {
        LogLine(logPath, "ERROR invoice window count=" invoiceCount)
        MsgBox("Facture Sage active introuvable ou ambigue.`nOuvre exactement une fenetre 'Facture - ...'.", "Sage Assistant")
        ExitApp(7)
    }
    LogLine(logPath, "FOUND invoice hwnd=" HwndHex(invoiceHwnd) " title=" invoiceTitle)

    addButtons := []
    for hwnd in WinGetControlsHwnd("ahk_id " invoiceHwnd) {
        try {
            cls := WinGetClass("ahk_id " hwnd)
            text := ControlGetText("ahk_id " hwnd)
            if (cls = "CamPopup" && text = "&Ajouter" && IsWindowVisible(hwnd)) {
                rect := GetWindowRect(hwnd)
                addButtons.Push(Map("hwnd", hwnd, "left", rect.left, "top", rect.top, "right", rect.right, "bottom", rect.bottom))
            }
        }
    }
    if addButtons.Length != 1 {
        LogLine(logPath, "ERROR add button count=" addButtons.Length)
        MsgBox("Bouton Sage '&Ajouter' introuvable ou ambigu dans la facture.", "Sage Assistant")
        ExitApp(8)
    }
    add := addButtons[1]
    centerX := Floor((add["left"] + add["right"]) / 2)
    centerY := Floor((add["top"] + add["bottom"]) / 2)
    LogLine(logPath, "FOUND add hwnd=" HwndHex(add["hwnd"]) " rect=" add["left"] "," add["top"] "," add["right"] "," add["bottom"] " center=" centerX "," centerY)
    return Map("mainHwnd", mainHwnd, "invoiceHwnd", invoiceHwnd, "invoiceTitle", invoiceTitle, "addHwnd", add["hwnd"], "addCenterX", centerX, "addCenterY", centerY)
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

EscapePowerShell(text) {
    return StrReplace(text, "'", "''")
}

QuoteArg(text) {
    return '"' StrReplace(text, '"', '\"') '"'
}

HwndHex(hwnd) {
    return Format("0x{:X}", Integer(hwnd))
}

LogLine(logPath, "DONE lines=" lines.Length)
ToolTip("Injection Sage terminee")
SetTimer(() => ToolTip(), -1500)
ExitApp(0)

SendTabs(count, delayMs) {
    Loop count {
        Send("{Tab}")
        Sleep(delayMs)
    }
}

WaitIfPaused() {
    global Paused, StopRequested
    while Paused && !StopRequested {
        Sleep(100)
    }
}

WaitNextLine(index, ref, logPath) {
    global NextRequested, StopRequested
    NextRequested := false
    ToolTip("Pret ligne " index " / " ref ". Ctrl+Alt+N pour envoyer.")
    LogLine(logPath, "WAIT line=" index " ref=" ref)
    while !NextRequested && !StopRequested {
        Sleep(100)
    }
    ToolTip()
}

EnsureSageActive(windowTitle, focusGuard, logPath, index, ref) {
    if !focusGuard {
        return
    }
    if !WinActive(windowTitle) {
        LogLine(logPath, "ERROR focus lost line=" index " ref=" ref)
        MsgBox("Sage n'est plus actif. Injection stoppee a la ligne " index " (" ref ").")
        ExitApp(3)
    }
}

LogLine(logPath, message) {
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
    profile["injection_mode"] := JsonGetString(profileText, "injection_mode", "keyboard_only")
    profile["window_title_contains"] := JsonGetString(profileText, "window_title_contains", "Sage")
    profile["start_position"] := JsonGetString(profileText, "start_position", "article_code")
    profile["delay_ms"] := JsonGetNumber(profileText, "delay_ms", 80)
    profile["after_article_tabs"] := JsonGetNumber(profileText, "after_article_tabs", 1)
    profile["after_description_tabs"] := JsonGetNumber(profileText, "after_description_tabs", 1)
    profile["after_quantity_tabs"] := JsonGetNumber(profileText, "after_quantity_tabs", 1)
    profile["validate_key"] := JsonGetString(profileText, "validate_key", "Enter")
    profile["focus_guard"] := JsonGetBool(profileText, "focus_guard", true)
    profile["step_mode"] := JsonGetBool(profileText, "step_mode", true)
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

    return Map("profile", profile, "lines", lines)
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
