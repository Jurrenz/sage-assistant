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
delayMs := profile.Has("delay_ms") ? Integer(profile["delay_ms"]) : 80
afterArticleTabs := profile.Has("after_article_tabs") ? Integer(profile["after_article_tabs"]) : 1
afterDescriptionTabs := profile.Has("after_description_tabs") ? Integer(profile["after_description_tabs"]) : 1
afterQuantityTabs := profile.Has("after_quantity_tabs") ? Integer(profile["after_quantity_tabs"]) : 1
validateKey := profile.Has("validate_key") ? profile["validate_key"] : "Enter"
focusGuard := profile.Has("focus_guard") ? ToBool(profile["focus_guard"]) : true
stepMode := profile.Has("step_mode") ? ToBool(profile["step_mode"]) : true
logPath := profile.Has("log_path") ? profile["log_path"] : A_ScriptDir "\sage_injection.log"

LogLine(logPath, "START queue=" queuePath " lines=" lines.Length)

MsgBox("Place le curseur dans Sage au debut de la ligne facture, puis clique OK.`n`nPause: Ctrl+Alt+P`nStop: Ctrl+Alt+S`nLigne suivante: Ctrl+Alt+N", "Sage Assistant")

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
    LogLine(logPath, "OK line=" index " ref=" line["ref"])
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
