"""Exercise rewind through the real TUI and read its terminal screen."""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ENTRY = REPO / "ui-tui" / "dist" / "entry.js"

pyte = pytest.importorskip("pyte", reason="pyte not installed (dev-only e2e)")
# importorskip, NOT a top-level import: `pty` does not exist on Windows, and
# a module-level ImportError is a pytest COLLECTION ERROR there — the win32
# skipif marker below never gets a chance to apply (same guard as
# test_tool_system_tools.py's pty import).
pty = pytest.importorskip("pty", reason="POSIX pty required")

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX pty required"),
    pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH"),
    pytest.mark.skipif(not ENTRY.exists(), reason="ui-tui/dist not built"),
]

FAKE_SERVER = textwrap.dedent(
    """
    import json, sys

    def emit(obj):
        print(json.dumps(obj), flush=True)

    emit({"type": "system", "subtype": "init", "session_id": "s1",
          "model": "fake-model", "tools": [], "permission_mode": "default",
          "protocol_version": "0.1.0", "cwd": "."})
    count = 0
    for line in sys.stdin:
        msg = json.loads(line)
        if msg.get("type") == "user":
            count += 1
            if "trigger provider error" in json.dumps(msg):
                emit({"type": "result", "subtype": "error", "session_id": "s1",
                      "num_turns": 0, "result": "provider returned 400: workspace-id required",
                      "error": "provider returned 400: workspace-id required", "is_error": True,
                      "duration_ms": 5, "usage": None, "cost": {}, "session_turns": count - 1})
                continue
            text = "obsolete answer" if count == 2 else "retained answer"
            emit({"type": "assistant", "session_id": "s1", "uuid": f"a{count}",
                  "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})
            emit({"type": "result", "subtype": "success", "session_id": "s1",
                  "num_turns": 1, "result": text, "duration_ms": 5, "is_error": False,
                  "usage": None, "total_cost_usd": 0.0, "cost": {}, "session_turns": count})
        elif msg.get("type") == "control_request":
            req = msg.get("request") or {}
            subtype = req.get("subtype", "")
            result = {}
            if subtype == "rewind_points":
                result = {"ok": True, "points": [
                    {"message_id": "u1", "text": "keep this request", "timestamp": None,
                     "files": [], "added": 0, "removed": 0, "can_restore_code": False},
                    {"message_id": "u2", "text": "restore this request", "timestamp": None,
                     "files": ["hello.ts"], "added": 3, "removed": 1, "can_restore_code": True}]}
                result["points"] = result["points"][:count]
            elif subtype == "rewind":
                if req.get("message_id") not in ("u1", "u2") or req.get("mode") != "conversation":
                    result = {"ok": False, "error": "Unexpected rewind selection"}
                else:
                    result = {"ok": True, "messages": [
                        {"role": "user", "text": "keep this request"},
                        {"role": "assistant", "text": "retained answer"}],
                        "prompt": "restore this request", "session_turns": 1,
                        "removed": 1, "restored_files": []}
                    if req.get("message_id") == "u1":
                        result.update(messages=[], prompt="keep this request", session_turns=0)
            emit({"type": "control_response", "response": {
                "subtype": subtype, "request_id": msg.get("request_id"), "response": result}})
    """
)


class _TuiSession:
    """The real TUI in a PTY, screen mirrored into a pyte emulator."""

    COLS, ROWS = 140, 40

    def __init__(self, tmp_path: Path, rows: int = 40, server_source: str = FAKE_SERVER):
        self.ROWS = rows
        server_path = tmp_path / "fake_agent_server.py"
        server_path.write_text(server_source, encoding="utf-8")
        env = {
            **os.environ,
            "TERM": "xterm-256color",
            "CLAWCODEX_TUI_INLINE": "1",
            "CLAWCODEX_WORKSPACE": str(tmp_path),
            "CLAWCODEX_CONFIG_DIR": str(tmp_path / "cfg"),
            "CLAWCODEX_AGENT_SERVER_CMD": json.dumps(
                [sys.executable, str(server_path)]
            ),
        }
        self.master, slave = pty.openpty()
        # Emulator and PTY must agree on geometry or wraps differ.
        import fcntl
        import struct
        import termios

        fcntl.ioctl(
            slave, termios.TIOCSWINSZ,
            struct.pack("HHHH", self.ROWS, self.COLS, 0, 0),
        )
        os.write(slave, b"HOST OUTPUT BEFORE CLAWCODEX\r\n")
        self.raw = bytearray()
        self.proc = subprocess.Popen(
            ["node", str(ENTRY)],
            stdin=slave, stdout=slave, stderr=slave,
            cwd=str(tmp_path), env=env, close_fds=True,
        )
        os.close(slave)
        self.screen = pyte.Screen(self.COLS, self.ROWS)
        self.stream = pyte.ByteStream(self.screen)

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if not ready:
                continue
            try:
                data = os.read(self.master, 65536)
            except OSError:
                return
            if not data:
                return
            self.raw.extend(data)
            self.stream.feed(data)

    def wait_for(self, needle: str, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            self.pump(0.2)
            if any(needle in row for row in self.screen.display):
                return True
        return False

    def send(self, text: str) -> None:
        os.write(self.master, text.encode())

    def row_with(self, needle: str) -> str | None:
        for row in self.screen.display:
            if needle in row:
                return row
        return None

    def row_index(self, needle: str) -> int:
        matches = [index for index, row in enumerate(self.screen.display) if needle in row]
        return max(matches) if matches else -1

    def submit(self, text: str, expected: str) -> None:
        self.send(text)
        assert self.wait_for(text, 5), self.dump()
        self.send("\r")
        assert self.wait_for(expected, 20), self.dump()
        self.pump(0.3)

    def dump(self) -> str:
        return "\n".join(
            row.rstrip() for row in self.screen.display if row.strip()
        )

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()  # reap — no zombie for the rest of the run
        os.close(self.master)


@pytest.fixture()
def tui(tmp_path):
    session = _TuiSession(tmp_path)
    yield session
    session.close()


def test_rewind_replaces_visible_history_and_restores_composer(tui):
    assert tui.wait_for("❯", 30), tui.dump()
    tui.send("keep this request")
    assert tui.wait_for("keep this request", 5), tui.dump()
    tui.send("\r")
    assert tui.wait_for("retained answer", 20), tui.dump()
    tui.pump(0.3)
    assert "a coding agent in your terminal" in tui.dump(), tui.dump()
    assert tui.row_index("❯") >= tui.ROWS - 8, tui.dump()
    tui.send("restore this request")
    assert tui.wait_for("restore this request", 5), tui.dump()
    tui.send("\r")
    assert tui.wait_for("obsolete answer", 20), tui.dump()
    tui.pump(0.3)
    tui.send("/rewind")
    assert tui.wait_for("/rewind", 5), tui.dump()
    tui.send("\r")
    assert tui.wait_for("Restore the code and/or conversation to the point before…", 10), tui.dump()
    tui.send("\x1b[A")
    tui.pump(0.2)
    tui.send("\r")
    assert tui.wait_for("Restore code and conversation", 5), tui.dump()
    tui.send("\x1b[B")
    tui.pump(0.2)
    tui.send("\r")
    tui.pump(0.8)
    assert "obsolete answer" not in tui.dump(), tui.dump()
    assert "retained answer" in tui.dump(), tui.dump()
    assert "Restore code and conversation" not in tui.dump(), tui.dump()
    assert "turns: 1" in tui.dump(), tui.dump()
    assert "a coding agent in your terminal" in tui.dump(), tui.dump()
    assert tui.row_index("❯") >= tui.ROWS - 8, tui.dump()
    tui.send(" edited")
    assert tui.wait_for("restore this request edited", 5), tui.dump()


def test_double_escape_opens_picker_and_escape_cancels(tui):
    assert tui.wait_for("❯", 30), tui.dump()
    tui.send("keep this request")
    assert tui.wait_for("keep this request", 5), tui.dump()
    tui.send("\r")
    assert tui.wait_for("retained answer", 20), tui.dump()
    tui.pump(0.3)
    tui.send("\x1b")
    tui.pump(0.1)
    assert "Restore the code and/or conversation to the point before…" not in tui.dump(), tui.dump()
    tui.send("\x1b")
    assert tui.wait_for("Restore the code and/or conversation to the point before…", 5), tui.dump()
    tui.send("\x1b")
    tui.pump(0.3)
    assert "Restore the code and/or conversation to the point before…" not in tui.dump(), tui.dump()
    assert "retained answer" in tui.dump(), tui.dump()

    tui.send("unsent draft")
    assert tui.wait_for("unsent draft", 5), tui.dump()
    tui.send("\x1b")
    tui.pump(0.1)
    tui.send("\x1b")
    tui.pump(0.3)
    assert "Restore the code and/or conversation to the point before…" not in tui.dump(), tui.dump()
    assert "unsent draft" in tui.dump(), tui.dump()


@pytest.mark.parametrize("rows", [24, 40])
def test_rewind_panel_leaves_conversation_above_and_cancel_preserves_it(tmp_path, rows):
    tui = _TuiSession(tmp_path, rows=rows)
    try:
        assert tui.wait_for("❯", 30), tui.dump()
        tui.submit("keep this request", "retained answer")
        tui.submit("restore this request", "obsolete answer")
        assert tui.row_index("❯") >= rows - 8, tui.dump()
        tui.submit("/rewind", "Restore the code and/or conversation to the point before…")
        title = tui.row_index("Rewind")
        answer = tui.row_index("obsolete answer")
        assert 0 <= answer < title, tui.dump()
        assert tui.row_index("Enter") < rows, tui.dump()
        assert tui.row_index("Enter") >= rows - 3, tui.dump()
        tui.send("\x1b")
        tui.pump(0.4)
        assert "Restore the code and/or conversation to the point before…" not in tui.dump(), tui.dump()
        assert "retained answer" in tui.dump(), tui.dump()
        assert "obsolete answer" in tui.dump(), tui.dump()
        assert tui.row_index("❯") >= rows - 8, tui.dump()
    finally:
        tui.close()


def test_first_response_keeps_logo_and_composer_at_screen_bottom(tui):
    assert tui.wait_for("❯", 30), tui.dump()
    assert "a coding agent in your terminal" in tui.dump(), tui.dump()
    tui.submit("keep this request", "retained answer")
    assert "a coding agent in your terminal" in tui.dump(), tui.dump()
    assert "keep this request" in tui.dump(), tui.dump()
    assert tui.row_index("❯") >= tui.ROWS - 8, tui.dump()


def test_provider_error_stays_in_transcript(tui):
    assert tui.wait_for("❯", 30), tui.dump()
    tui.submit("trigger provider error", "provider returned 400: workspace-id required")
    tui.send("next draft")
    assert tui.wait_for("next draft", 5), tui.dump()
    assert "provider returned 400: workspace-id required" in tui.dump(), tui.dump()
    assert "trigger provider error" in tui.dump(), tui.dump()
    assert tui.row_index("❯") >= tui.ROWS - 8, tui.dump()


def test_fullscreen_uses_alternate_buffer_and_restores_it_on_exit(tui):
    assert tui.wait_for("❯", 30), tui.dump()
    assert "HOST OUTPUT BEFORE CLAWCODEX" not in tui.dump(), tui.dump()
    entered = bytes(tui.raw).index(b"\x1b[?1049h")
    assert b"\x1b[2J" not in tui.raw[:entered]
    assert b"\x1b[3J" not in tui.raw[:entered]
    tui.send("/exit")
    assert tui.wait_for("/exit", 5), tui.dump()
    tui.send("\r")
    end = time.time() + 10
    while tui.proc.poll() is None and time.time() < end:
        tui.pump(0.2)
    tui.pump(0.2)
    assert tui.proc.poll() == 0, tui.dump()
    assert b"\x1b[?1049l" in tui.raw[entered:]


def test_fullscreen_resize_keeps_history_and_composer_inside_viewport(tui):
    import fcntl
    import signal
    import struct
    import termios

    assert tui.wait_for("❯", 30), tui.dump()
    tui.submit("keep this request", "retained answer")
    for cols, rows in [(100, 24), (140, 40)]:
        tui.COLS, tui.ROWS = cols, rows
        tui.screen.resize(lines=rows, columns=cols)
        fcntl.ioctl(tui.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        os.kill(tui.proc.pid, signal.SIGWINCH)
        tui.pump(0.8)
        assert "retained answer" in tui.dump(), tui.dump()
        assert "keep this request" in tui.dump(), tui.dump()
        assert tui.row_index("❯") >= rows - 8, tui.dump()
        assert tui.row_index("tokens:") == rows - 1, tui.dump()
    assert "a coding agent in your terminal" in tui.dump(), tui.dump()



def test_rewind_to_first_prompt_keeps_logo_on_empty_history(tui):
    assert tui.wait_for("❯", 30), tui.dump()
    tui.submit("keep this request", "retained answer")
    tui.submit("/rewind", "Restore the code and/or conversation to the point before…")
    tui.send("\x1b[A")
    tui.pump(0.2)
    tui.send("\r")
    assert tui.wait_for("Restore to before this message?", 5), tui.dump()
    tui.send("\r")
    tui.pump(0.8)
    assert "retained answer" not in tui.dump(), tui.dump()
    assert "a coding agent in your terminal" in tui.dump(), tui.dump()
    assert "turns: 0" in tui.dump(), tui.dump()
    assert "keep this request" in tui.row_with("❯"), tui.dump()
    assert tui.row_index("❯") >= tui.ROWS - 8, tui.dump()


def _real_rewind_server(response_shape: str) -> str:
    """Use the real conversation, checkpoint selection and rewind response."""
    return "REPO = " + repr(str(REPO)) + "\nSHAPE = " + repr(response_shape) + "\n" + textwrap.dedent(
        r"""
        import json, sys
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        sys.path.insert(0, REPO)
        from src.agent.conversation import Conversation
        from src.server.agent_server import AgentServerConfig, _AgentSession

        def emit(obj):
            print(json.dumps(obj), flush=True)

        workspace = Path.cwd()
        server = _AgentSession(session_id="s1", cwd=str(workspace),
            config=AgentServerConfig(), loop=MagicMock(), out_queue=MagicMock())
        server._emit = emit
        server.session = SimpleNamespace(conversation=Conversation())
        conv = server.session.conversation
        patch("src.server.agent_server._sessions_dir", return_value=workspace / "sessions").start()
        emit({"type": "system", "subtype": "init", "session_id": "s1",
              "model": "fake-model", "tools": [], "permission_mode": "default",
              "protocol_version": "0.1.0", "cwd": str(workspace)})
        count = 0
        for line in sys.stdin:
            msg = json.loads(line)
            if msg.get("type") == "user":
                count += 1
                conv.add_user_message(msg["message"]["content"])
                server._rewind_history.begin(conv.messages[-1].uuid)
                if count == 1 and SHAPE == "error":
                    error = "provider error FIRST_ERROR_KEPT"
                    server._display_messages[conv.messages[-1].uuid] = [{"role": "system", "text": "error: " + error}]
                    emit({"type": "result", "subtype": "error", "session_id": "s1",
                          "num_turns": 0, "result": error, "error": error, "is_error": True,
                          "duration_ms": 5, "usage": None, "cost": {}, "session_turns": 0})
                    continue
                if count == 2:
                    blocks = [{"type": "text", "text": "SECOND_REPLY_REMOVED"}]
                elif SHAPE == "blocks":
                    blocks = [{"type": "text", "text": "KEEP_HEAD in the first text block"},
                              {"type": "text", "text": "KEEP_TAIL in the second text block"}]
                elif SHAPE == "long":
                    body = "KEEP_HEAD\n\n" + "\n".join(f"- preserved line {i:03d}" for i in range(90)) + "\n\nKEEP_TAIL"
                    blocks = [{"type": "text", "text": body}]
                else:
                    blocks = [{"type": "text", "text": "KEEP_HEAD and KEEP_TAIL"}]
                if count == 1 and SHAPE == "tools":
                    leading = [{"type": "text", "text": "KEEP_HEAD before the tool"},
                        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "preserved.py"}}]
                    conv.add_assistant_message(leading)
                    emit({"type": "assistant", "session_id": "s1", "uuid": "a-before-tool",
                          "message": {"role": "assistant", "content": leading}})
                    conv.add_tool_result_message("t1", "tool result")
                    emit({"type": "user", "session_id": "s1", "message": {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "tool result"}]}})
                    blocks = [{"type": "text", "text": "KEEP_TAIL after the tool"}]
                if count == 1 and SHAPE == "tools_only":
                    blocks = [{"type": "tool_use", "id": "t-only", "name": "Write", "input": {"file_path": "preserved.py"}}]
                conv.add_assistant_message(blocks if SHAPE != "plain" else blocks[0]["text"])
                emit({"type": "assistant", "session_id": "s1", "uuid": f"a{count}",
                      "message": {"role": "assistant", "content": blocks}})
                if count == 1 and SHAPE == "tools_only":
                    conv.add_tool_result_message("t-only", "file written")
                    emit({"type": "user", "session_id": "s1", "message": {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": "t-only", "content": "file written"}]}})
                server._stats_turns = count
                emit({"type": "result", "subtype": "success", "session_id": "s1", "num_turns": 1,
                      "result": "\n\n".join(block.get("text", "") for block in blocks), "duration_ms": 5, "is_error": False,
                      "usage": None, "total_cost_usd": 0.0, "cost": {}, "session_turns": count})
            elif msg.get("type") == "control_request":
                req = msg.get("request") or {}
                subtype = req.get("subtype", "")
                if subtype == "rewind_points":
                    server._do_rewind_points(msg["request_id"])
                elif subtype == "rewind":
                    server._do_rewind(msg["request_id"], req.get("message_id"), req.get("mode"))
                    (workspace / "remaining-conversation.json").write_text(json.dumps({"conversation": conv.to_dict(), "display_messages": server._display_messages}))
                else:
                    emit({"type": "control_response", "response": {"subtype": subtype,
                          "request_id": msg.get("request_id"), "response": {}}})
        """
    )


@pytest.mark.parametrize("rows", [24, 40])
@pytest.mark.parametrize("response_shape", ["plain", "blocks", "tools", "tools_only", "long", "error"])
def test_second_prompt_rewind_preserves_real_first_response(tmp_path, rows, response_shape):
    tui = _TuiSession(tmp_path, rows=rows, server_source=_real_rewind_server(response_shape))
    try:
        assert tui.wait_for("❯", 30), tui.dump()
        marker = {"tools_only": "preserved.py", "error": "FIRST_ERROR_KEPT"}.get(response_shape, "KEEP_TAIL")
        tui.submit("FIRST_REQUEST_KEPT", marker)
        tui.submit("SECOND_REQUEST_EDIT", "SECOND_REPLY_REMOVED")
        tui.submit("/rewind", "Restore the code and/or conversation to the point before…")
        tui.send("\x1b[A")
        tui.pump(0.2)
        tui.send("\r")
        assert tui.wait_for("Restore to before this message?", 5), tui.dump()
        tui.send("\r")
        assert tui.wait_for("turns: 1", 5), tui.dump()
        tui.pump(0.4)
        persisted = (tmp_path / "remaining-conversation.json").read_text()
        assert "FIRST_REQUEST_KEPT" in persisted
        if response_shape == "tools_only":
            assert '"name": "Write"' in persisted and "preserved.py" in persisted
        elif response_shape == "error":
            assert "FIRST_ERROR_KEPT" in persisted
        else:
            assert "KEEP_HEAD" in persisted and "KEEP_TAIL" in persisted
        assert "SECOND_REQUEST_EDIT" not in persisted
        assert "SECOND_REPLY_REMOVED" not in persisted
        assert "SECOND_REPLY_REMOVED" not in tui.dump(), tui.dump()
        assert marker in tui.dump(), tui.dump()
        if response_shape == "long":
            assert "KEEP_HEAD" not in tui.dump(), tui.dump()
            for _ in range(25):
                tui.send("\x1b[5~")
                tui.pump(0.1)
                if "KEEP_HEAD" in tui.dump():
                    break
            assert "KEEP_HEAD" in tui.dump(), tui.dump()
        elif response_shape not in ("tools_only", "error"):
            assert "KEEP_HEAD" in tui.dump(), tui.dump()
        assert "SECOND_REQUEST_EDIT" in tui.screen.display[tui.row_index("❯")], tui.dump()
    finally:
        tui.close()
