from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


from src.agent.conversation import Conversation
from src.server.agent_server import AgentServerConfig, _AgentSession
from src.server.rewind import RewindHistory
from src.tool_system.context import ToolContext
from src.tool_system.tools.write import _write_call as write_tool
from src.tool_system.tools.edit import _edit_call as edit_tool


def session(tmp_path):
    emitted = []
    server = _AgentSession(session_id="live", cwd=str(tmp_path),
                           config=AgentServerConfig(), loop=MagicMock(), out_queue=MagicMock())
    server._emit = emitted.append
    server.session = SimpleNamespace(conversation=Conversation())
    return server, emitted


def reply(emitted):
    return emitted[-1]["response"]["response"]


def add_turn(server, text):
    conv = server.session.conversation
    conv.add_user_message(text)
    message_id = conv.messages[-1].uuid
    server._rewind_history.begin(message_id)
    conv.add_assistant_message("done")
    return message_id


def test_candidates_exclude_tool_results_and_injected_context(tmp_path):
    server, emitted = session(tmp_path)
    mid = add_turn(server, "first")
    conv = server.session.conversation
    conv.add_user_message("<system-reminder>context</system-reminder>")
    conv.add_message("user", "meta", isMeta=True)
    conv.add_tool_result_message("tool1", "result")
    server._do_rewind_points("list")
    assert reply(emitted)["points"] == [{"message_id": mid, "text": "first",
        "timestamp": conv.messages[0].timestamp, "files": [], "added": 0,
        "removed": 0, "can_restore_code": True}]


def test_restore_repaints_history_prefills_prompt_and_preserves_original(tmp_path):
    server, emitted = session(tmp_path)
    add_turn(server, "first")
    mid = add_turn(server, "second")
    server._stats_turns = 2
    with patch("src.server.agent_server._sessions_dir", return_value=tmp_path):
        asyncio.run(server._handle_control_request({"request_id": "restore", "request": {
            "subtype": "rewind", "message_id": mid, "mode": "conversation"}}))
    result = reply(emitted)
    assert result["ok"] and result["prompt"] == "second"
    assert result["messages"] == [{"role": "user", "text": "first"},
                                  {"role": "assistant", "text": "done"}]
    assert result["session_turns"] == 1
    archives = [p for p in tmp_path.glob("*.json") if p.stem != "live"]
    assert len(archives) == 1
    assert json.loads(archives[0].read_text())["conversation"]["messages"][2]["content"] == "second"
    assert len(json.loads((tmp_path / "live.json").read_text())["conversation"]["messages"]) == 2


def test_both_restores_existing_file_and_removes_new_file(tmp_path):
    server, emitted = session(tmp_path)
    file = tmp_path / "existing.py"
    file.write_bytes(b"original\r\n")
    mid = add_turn(server, "edit")
    server._rewind_history.write(file, "changed\n")
    created = tmp_path / "created.py"
    server._rewind_history.write(created, "new\n")
    with patch("src.server.agent_server._sessions_dir", return_value=tmp_path / "sessions"):
        server._do_rewind("restore", mid, "both")
    assert reply(emitted)["ok"]
    assert file.read_bytes() == b"original\r\n"
    assert not created.exists()
    assert server.session.conversation.messages == []
    assert json.loads((tmp_path / "sessions/live.json").read_text())["conversation"]["messages"] == []


def test_manual_change_blocks_entire_restore_without_truncating_conversation(tmp_path):
    server, emitted = session(tmp_path)
    files = [tmp_path / "a", tmp_path / "b"]
    for file in files:
        file.write_text("before")
    mid = add_turn(server, "edit")
    for file in files:
        server._rewind_history.write(file, "tool")
    files[1].write_text("manual")
    with patch("src.server.agent_server._sessions_dir", return_value=tmp_path / "sessions"):
        server._do_rewind("restore", mid, "both")
    assert not reply(emitted)["ok"]
    assert "outside tracked tools" in reply(emitted)["error"]
    assert files[0].read_text() == "tool"
    assert files[1].read_text() == "manual"
    assert len(server.session.conversation.messages) == 2


def test_code_only_keeps_transcript_and_can_restore_earlier_checkpoint(tmp_path):
    server, emitted = session(tmp_path)
    file = tmp_path / "code"
    file.write_text("zero")
    first = add_turn(server, "one")
    server._rewind_history.write(file, "one")
    second = add_turn(server, "two")
    server._rewind_history.write(file, "two")
    with patch("src.server.agent_server._sessions_dir", return_value=tmp_path / "sessions"):
        server._do_rewind("restore", second, "code")
        assert reply(emitted)["ok"] and len(server.session.conversation.messages) == 4
        assert file.read_text() == "one"
        server._do_rewind("restore", first, "code")
    assert reply(emitted)["ok"] and file.read_text() == "zero"


def test_conversation_only_preserves_discarded_code_for_later_restore(tmp_path):
    server, emitted = session(tmp_path)
    file = tmp_path / "code"
    file.write_text("zero")
    first = add_turn(server, "one")
    server._rewind_history.write(file, "one")
    second = add_turn(server, "two")
    server._rewind_history.write(file, "two")
    with patch("src.server.agent_server._sessions_dir", return_value=tmp_path / "sessions"):
        server._do_rewind("restore", second, "conversation")
        assert file.read_text() == "two"
        server._do_rewind("restore", first, "both")
    assert reply(emitted)["ok"] and file.read_text() == "zero"


def test_legacy_history_has_no_code_restore_and_numeric_target_fails(tmp_path):
    server, emitted = session(tmp_path)
    server.session.conversation.add_user_message("old")
    server._do_rewind_points("list")
    assert reply(emitted)["points"][0]["can_restore_code"] is False
    mid = server.session.conversation.messages[0].uuid
    with patch("src.server.agent_server._sessions_dir", return_value=tmp_path / "sessions"):
        server._do_rewind("restore", mid, "both")
    assert not reply(emitted)["ok"]
    server._do_rewind("restore", 1)
    assert not reply(emitted)["ok"] and len(server.session.conversation.messages) == 1


def test_checkpoints_survive_save_and_resume(tmp_path):
    server, _ = session(tmp_path)
    file = tmp_path / "code"
    file.write_text("before")
    mid = add_turn(server, "edit")
    server._rewind_history.write(file, "after")
    with patch("src.server.agent_server._sessions_dir", return_value=tmp_path / "sessions"):
        server._save_session()
        resumed, emitted = session(tmp_path)
        resumed._do_resume("resume", "live")
        assert reply(emitted)["ok"]
        resumed._do_rewind("restore", mid, "both")
    assert reply(emitted)["ok"] and file.read_text() == "before"


def test_file_tools_record_exact_before_state(tmp_path):
    context = ToolContext(workspace_root=tmp_path)
    history = context.rewind_history = RewindHistory()
    history.begin("prompt")
    file = tmp_path / "file.py"
    write_tool({"file_path": str(file), "content": "one\n"}, context)
    edit_tool({"file_path": str(file), "old_string": "one", "new_string": "two"}, context)
    assert history.preview(["prompt"])["added"] == 1
    assert history.restore(["prompt"]) == [str(file)]
    assert not file.exists()


def test_restore_rejects_symlink_and_hardlink_replacements(tmp_path):
    import os

    file = tmp_path / "code"
    other = tmp_path / "other"
    file.write_text("before")
    history = RewindHistory()
    history.begin("prompt")
    history.write(file, "after")
    other.write_text("after")
    file.unlink()
    file.symlink_to(other)
    with pytest.raises(ValueError, match="symbolic link"):
        history.restore(["prompt"])
    assert other.read_text() == "after"
    file.unlink()
    os.link(other, file)
    with pytest.raises(ValueError, match="independent regular file"):
        history.restore(["prompt"])
    assert other.read_text() == "after"


def test_busy_session_cannot_list_or_restore(tmp_path):
    server, emitted = session(tmp_path)
    mid = add_turn(server, "prompt")
    server._inbox.put("queued work")
    server._do_rewind_points("list")
    assert not reply(emitted)["ok"]
    server._do_rewind("restore", mid)
    assert not reply(emitted)["ok"]
    assert len(server.session.conversation.messages) == 2


def test_notebook_edit_tracks_original_bytes(tmp_path):
    from src.tool_system.tools.notebook_edit import _notebook_edit_call

    file = tmp_path / "test.ipynb"
    original = b'{"cells":[{"cell_type":"code","source":["old"],"metadata":{},"outputs":[],"execution_count":null,"id":"cell-1"}],"metadata":{},"nbformat":4,"nbformat_minor":5}\n'
    file.write_bytes(original)
    context = ToolContext(workspace_root=tmp_path)
    history = context.rewind_history = RewindHistory()
    history.begin("prompt")
    context.mark_file_read(file)
    _notebook_edit_call({"notebook_path": str(file), "cell_id": "cell-1", "new_source": "new"}, context)
    assert file.read_bytes() != original
    history.restore(["prompt"])
    assert file.read_bytes() == original


def test_manual_change_between_turns_blocks_earlier_restore(tmp_path):
    file = tmp_path / "code"
    file.write_text("original")
    history = RewindHistory()
    history.begin("first")
    history.write(file, "first tool edit")
    file.write_text("manual edit")
    history.begin("second")
    history.write(file, "second tool edit")
    assert not history.preview(["first", "second"])["can_restore_code"]
    with pytest.raises(ValueError, match="between checkpoints"):
        history.restore(["first", "second"])
    assert file.read_text() == "second tool edit"
    history.restore(["second"])
    assert file.read_text() == "manual edit"


def test_failed_archive_save_leaves_code_and_conversation_intact(tmp_path):
    server, emitted = session(tmp_path)
    file = tmp_path / "code"
    file.write_text("before")
    mid = add_turn(server, "edit")
    server._rewind_history.write(file, "after")
    server._save_session = lambda: False
    server._do_rewind("restore", mid, "both")
    assert not reply(emitted)["ok"]
    assert "preserve the conversation" in reply(emitted)["error"]
    assert file.read_text() == "after"
    assert len(server.session.conversation.messages) == 2
