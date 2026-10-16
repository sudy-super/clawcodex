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
            elif subtype == "rewind":
                if req.get("message_id") != "u2" or req.get("mode") != "conversation":
                    result = {"ok": False, "error": "Unexpected rewind selection"}
                else:
                    result = {"ok": True, "messages": [
                        {"role": "user", "text": "keep this request"},
                        {"role": "assistant", "text": "retained answer"}],
                        "prompt": "restore this request", "session_turns": 1,
                        "removed": 1, "restored_files": []}
            emit({"type": "control_response", "response": {
                "subtype": subtype, "request_id": msg.get("request_id"), "response": result}})
    """
)


class _TuiSession:
    """The real TUI in a PTY, screen mirrored into a pyte emulator."""

    COLS, ROWS = 140, 40

    def __init__(self, tmp_path: Path):
        server_path = tmp_path / "fake_agent_server.py"
        server_path.write_text(FAKE_SERVER, encoding="utf-8")
        env = {
            **os.environ,
            "TERM": "xterm-256color",
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
    tui.send("restore this request")
    assert tui.wait_for("restore this request", 5), tui.dump()
    tui.send("\r")
    assert tui.wait_for("obsolete answer", 20), tui.dump()
    tui.pump(0.3)
    tui.send("/rewind")
    assert tui.wait_for("/rewind", 5), tui.dump()
    tui.send("\r")
    assert tui.wait_for("Restore to before…", 10), tui.dump()
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
    assert "Restore to before…" not in tui.dump(), tui.dump()
    tui.send("\x1b")
    assert tui.wait_for("Restore to before…", 5), tui.dump()
    tui.send("\x1b")
    tui.pump(0.3)
    assert "Restore to before…" not in tui.dump(), tui.dump()
    assert "retained answer" in tui.dump(), tui.dump()

    tui.send("unsent draft")
    assert tui.wait_for("unsent draft", 5), tui.dump()
    tui.send("\x1b")
    tui.pump(0.1)
    tui.send("\x1b")
    tui.pump(0.3)
    assert "Restore to before…" not in tui.dump(), tui.dump()
    assert "unsent draft" in tui.dump(), tui.dump()
