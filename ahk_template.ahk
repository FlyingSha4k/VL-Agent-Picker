#Requires AutoHotkey v2.0
#SingleInstance Force
#NoTrayIcon
Persistent
CoordMode "Mouse", "Screen"

; AP-SERVER-V2
; AHK запускается вместе с Agent Picker и ждёт команд. Править можно только блок
; "ВЫБОР АГЕНТА". После правки нажми "ПЕРЕЗАПУСТИТЬ AHK" в настройках программы.

; ===================== ВЫБОР АГЕНТА =====================
Repeats := 4   ; сколько раз повторить пару "агент + выбрать"

PickAgent(Name, AgentX, AgentY, LockX, LockY) {
    Loop Repeats {
        MouseClick "left", AgentX, AgentY, 1, 0, "D"
        Sleep 25
        MouseClick "left", AgentX, AgentY, 1, 0, "U"

        Sleep 30

        MouseClick "left", LockX, LockY, 1, 0, "D"
        Sleep 25
        MouseClick "left", LockX, LockY, 1, 0, "U"

        Sleep 60
    }
}

; ===================== СЛУЖЕБНАЯ ЧАСТЬ (не менять) =====================
Busy := false
Srv := Gui(, "AgentPickerAHK")
OnMessage(0x4A, OnCopyData)   ; WM_COPYDATA

OnCopyData(wParam, lParam, msg, hwnd) {
    cmd := StrGet(NumGet(lParam, 2 * A_PtrSize, "Ptr"), "UTF-16")
    p := StrSplit(cmd, "|")
    try {
        switch p[1] {
            case "PICK":     ; PICK|имя|ax|ay|lx|ly - выполняется в фоне, ответ сразу
                SetTimer(RunPick.Bind(p[2], Integer(p[3]), Integer(p[4]), Integer(p[5]), Integer(p[6])), -1)
            case "SCROLL":   ; SCROLL|cx|cy|nx|ny|вверх|вниз|щелчков_вверх - ответ после прокрутки
                DoScroll(Integer(p[2]), Integer(p[3]), Integer(p[4]), Integer(p[5]), Integer(p[6]), Integer(p[7]), Integer(p[8]))
            case "EXIT":
                SetTimer(() => ExitApp(), -1)
        }
    }
    return 1
}

RunPick(Name, AX, AY, LX, LY) {
    global Busy
    if Busy
        return
    Busy := true
    try PickAgent(Name, AX, AY, LX, LY)
    Busy := false
}

DoScroll(cx, cy, nx, ny, toTop, down, topNotches) {
    MouseMove cx, cy, 0
    Sleep 40
    if toTop {
        MouseClick "WheelUp", cx, cy, topNotches, 0
        Sleep 80
    }
    Loop down {
        MouseClick "WheelDown", cx, cy, 1, 0
        Sleep 20
    }
    Sleep 40
    MouseMove nx, ny, 0
}
