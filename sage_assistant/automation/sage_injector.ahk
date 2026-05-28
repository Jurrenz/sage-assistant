#Requires AutoHotkey v2.0
#SingleInstance Force

; AutoHotkey v2 injector for Sage Assistant.
; Hotkeys:
;   Ctrl+Alt+P = pause/resume
;   Ctrl+Alt+S = stop immediately

global StopRequested := false
global Paused := false

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

MsgBox("Place le curseur dans Sage au debut de la ligne facture, puis clique OK.`n`nPause: Ctrl+Alt+P`nStop: Ctrl+Alt+S", "Sage Assistant")

if !WinExist(windowTitle) {
    MsgBox("Fenetre Sage introuvable avec le titre contenant: " windowTitle)
    ExitApp(1)
}

WinActivate(windowTitle)
WinWaitActive(windowTitle, , 5)

for _, line in lines {
    WaitIfPaused()
    if StopRequested {
        ExitApp(2)
    }
    SendText(line["article_code"])
    Sleep(delayMs)
    SendTabs(afterArticleTabs, delayMs)

    SendText(line["description"])
    Sleep(delayMs)
    SendTabs(afterDescriptionTabs, delayMs)

    SendText(String(line["quantity"]))
    Sleep(delayMs)
    SendTabs(afterQuantityTabs, delayMs)

    SendText(line["unit_price_ht"])
    Sleep(delayMs)
    Send("{" validateKey "}")
    Sleep(delayMs)
}

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

; Minimal JSON parser for AHK v2 based on JXON public-domain style.
Jxon_Load(&src, args*) {
    key := "", is_key := false
    stack := [tree := []]
    is_arr := { (tree): 1 }
    next := '"{[01234567890-tfn'
    pos := 0
    while ((ch := SubStr(src, ++pos, 1)) != "") {
        if InStr(" `t`n`r", ch)
            continue
        if !InStr(next, ch, true) {
            testArr := StrSplit(SubStr(src, 1, pos), "`n")
            ln := testArr.Length
            col := pos - InStr(src, "`n",, -(StrLen(src)-pos+1))
            throw Error("Unexpected character '" ch "' at line " ln " col " col)
        }
        obj := stack[stack.Length]
        if (ch = "{") {
            val := Map()
            if IsObject(obj) {
                if is_arr.Has(obj)
                    obj.Push(val)
                else
                    obj[key] := val
            }
            stack.Push(val)
            next := '"}'
        } else if (ch = "[") {
            val := []
            if IsObject(obj) {
                if is_arr.Has(obj)
                    obj.Push(val)
                else
                    obj[key] := val
            }
            stack.Push(val), is_arr[val] := 1
            next := '"{[0123456789-tfn]'
        } else if (ch = "}") || (ch = "]") {
            stack.Pop()
            next := stack.Length > 1 ? ",]}" : ""
        } else if (ch = ",") {
            next := is_arr.Has(obj) ? '"{[0123456789-tfn' : '"'
        } else if (ch = ":") {
            is_key := false
            next := '"{[0123456789-tfn'
        } else {
            if (ch = '"') {
                val := Jxon_ParseString(src, &pos)
            } else {
                m := ""
                RegExMatch(SubStr(src, pos), "^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null", &m)
                valText := m[0]
                pos += StrLen(valText) - 1
                if (valText = "true")
                    val := true
                else if (valText = "false")
                    val := false
                else if (valText = "null")
                    val := ""
                else
                    val := InStr(valText, ".") ? Float(valText) : Integer(valText)
            }
            if IsObject(obj) {
                if is_arr.Has(obj) {
                    obj.Push(val)
                    next := ",]"
                } else if !is_key {
                    key := val, is_key := true, next := ":"
                } else {
                    obj[key] := val, is_key := false, next := ",}"
                }
            }
        }
    }
    return tree[1]
}

Jxon_ParseString(src, &pos) {
    out := ""
    while ((ch := SubStr(src, ++pos, 1)) != "") {
        if (ch = '"')
            break
        if (ch = "\") {
            ch := SubStr(src, ++pos, 1)
            if (ch = '"') || (ch = "\") || (ch = "/")
                out .= ch
            else if (ch = "b")
                out .= Chr(8)
            else if (ch = "f")
                out .= Chr(12)
            else if (ch = "n")
                out .= "`n"
            else if (ch = "r")
                out .= "`r"
            else if (ch = "t")
                out .= "`t"
            else if (ch = "u") {
                hex := SubStr(src, pos + 1, 4)
                out .= Chr(Integer("0x" hex))
                pos += 4
            }
        } else {
            out .= ch
        }
    }
    return out
}
