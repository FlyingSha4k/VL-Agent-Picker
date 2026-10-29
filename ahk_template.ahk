#Requires AutoHotkey v2.0
#SingleInstance Force
CoordMode "Mouse", "Screen"

; Агент: {{NAME}}
AgentX := {{AGENT_X}}
AgentY := {{AGENT_Y}}
LockX := {{LOCK_X}}
LockY := {{LOCK_Y}}
Repeats := 4   ; сколько раз повторить пару "агент + выбрать"

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
ExitApp
