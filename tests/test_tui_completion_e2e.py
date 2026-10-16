"""Read completion rendering and keyboard interaction in the real terminal UI."""
from __future__ import annotations

import pytest

from test_tui_rewind_e2e import FAKE_SERVER, _TuiSession, pytestmark


class _CompletionSession(_TuiSession):
    def __init__(self, tmp_path, cols, source=FAKE_SERVER):
        self.COLS = cols
        super().__init__(tmp_path, rows=24, server_source=source)


@pytest.mark.parametrize("cols", [40, 140])
def test_exit_completion_is_below_input_without_a_box_and_keeps_key_actions(tmp_path, cols, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "3")
    tui = _CompletionSession(tmp_path, cols)
    try:
        assert tui.wait_for("❯", 30), tui.dump()
        tui.send("/ex")
        assert tui.wait_for("Exit clawcodex", 5), tui.dump()
        candidate_row = tui.row_index("Exit clawcodex")
        input_row = next(i for i, line in enumerate(tui.screen.display) if line.strip() == "❯ /ex")
        assert candidate_row > input_row, tui.dump()
        candidate = tui.screen.display[candidate_row]
        assert "❯ /exit" in candidate, tui.dump()
        assert not any(char in candidate for char in "╔╗╚╝║│"), tui.dump()
        start = candidate.index("/exit")
        assert tui.screen.buffer[candidate_row][start].fg == "a8b2f3"
        assert tui.screen.buffer[candidate_row][start].bg == "default"
        description = candidate.index("Exit clawcodex")
        assert tui.screen.buffer[candidate_row][description].fg == "a8b2f3"
        tui.send("\x1b")
        tui.pump(0.3)
        assert "Exit clawcodex" not in tui.dump(), tui.dump()
        assert "/ex" in tui.dump(), tui.dump()
        tui.send("i")
        assert tui.wait_for("Exit clawcodex", 5), tui.dump()
        tui.send("\t")
        tui.pump(0.3)
        assert "❯ /exit" in tui.dump(), tui.dump()
        assert tui.proc.poll() is None
        tui.send("\x15")
        tui.pump(0.2)
        tui.send("/ex")
        assert tui.wait_for("Exit clawcodex", 5), tui.dump()
        tui.send("\r")
        tui.pump(0.3)
        assert "❯ /exit" in tui.dump(), tui.dump()
        assert tui.proc.poll() is None
    finally:
        tui.close()


@pytest.mark.parametrize("cols", [40, 140])
def test_registered_command_selection_moves_with_arrows_and_tab(tmp_path, cols, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "3")
    tui = _CompletionSession(tmp_path, cols)
    try:
        assert tui.wait_for("❯", 30), tui.dump()
        tui.send("/")
        assert tui.wait_for("❯ /help", 5), tui.dump()
        row = tui.row_index("Clear the conversation")
        name = tui.screen.display[row].index("/clear")
        description = tui.screen.display[row].index("Clear the conversation")
        assert tui.screen.buffer[row][name].fg == "ffffff"
        assert tui.screen.buffer[row][description].fg == "8e8e8e"
        assert tui.screen.buffer[row][name].bg == "default"
        tui.send("\x1b[B")
        assert tui.wait_for("❯ /clear", 5), tui.dump()
        tui.send("\x1b[A")
        assert tui.wait_for("❯ /help", 5), tui.dump()
        tui.send("\x1b[B")
        assert tui.wait_for("❯ /clear", 5), tui.dump()
        tui.send("\t")
        tui.pump(0.3)
        assert "❯ /clear" in tui.dump(), tui.dump()
        assert tui.proc.poll() is None
        assert tui.row_index("tokens:") == tui.ROWS - 1, tui.dump()
    finally:
        tui.close()
