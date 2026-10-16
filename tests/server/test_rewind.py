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


class _RewindProvider:
    model = "rewind-test"

    def __init__(self, *, streaming=False, tool=False, error=False):
        self.streaming = streaming
        self.tool = tool
        self.error = error
        self.calls = []

    def chat(self, messages, tools=None, **kwargs):
        from src.providers.base import ChatResponse

        self.calls.append(messages)
        if self.error and len(self.calls) == 1:
            raise ValueError("provider 400: workspace-id required")
        if self.tool and len(self.calls) == 1:
            return ChatResponse(content="model 1 before tool", model=self.model,
                usage={"input_tokens": 3, "output_tokens": 2}, finish_reason="tool_use",
                tool_uses=[{"id": "write-1", "name": "Write", "input": {
                    "file_path": self.file_path, "content": "first"}}])
        answer = "model 1" if len(self.calls) == (2 if self.tool else 1) else "model 2"
        return ChatResponse(content=answer, model=self.model,
            usage={"input_tokens": 3, "output_tokens": 2}, finish_reason="stop")

    def chat_stream_response(self, messages, tools=None, on_text_chunk=None, **kwargs):
        if not self.streaming:
            raise NotImplementedError
        result = self.chat(messages, tools=tools, **kwargs)
        if on_text_chunk and result.content:
            for chunk in [result.content[:4], result.content[4:]]:
                on_text_chunk(chunk)
        return result


def real_turn_session(tmp_path, provider):
    from src.tool_system.registry import ToolRegistry
    from src.tool_system.tools.write import WriteTool

    server, emitted = session(tmp_path)
    server.provider = provider
    server.tool_registry = ToolRegistry([WriteTool])
    server.tool_context = ToolContext(workspace_root=tmp_path)
    server._run_user_prompt_submit_hooks = lambda prompt: None
    server._fire_session_start_once = lambda: None
    return server, emitted


@pytest.mark.parametrize("streaming,tool", [(False, False), (True, False), (True, True)])
def test_real_agent_turn_rewind_keeps_first_model_reply(tmp_path, streaming, tool):
    provider = _RewindProvider(streaming=streaming, tool=tool)
    provider.file_path = str(tmp_path / "created.txt")
    server, emitted = real_turn_session(tmp_path, provider)
    with patch("src.server.agent_server._sessions_dir", return_value=tmp_path / "sessions"):
        assert server._run_turn("user 1")["subtype"] == "success"
        assert server._run_turn("user 2")["subtype"] == "success"
        before = server.session.conversation.to_dict()
        second = next(m for m in server.session.conversation.messages if m.content == "user 2")
        server._do_rewind("restore", second.uuid, "conversation")
    result = reply(emitted)
    assert result["ok"]
    assert {"role": "assistant", "text": "model 1"} in result["messages"]
    assert {"role": "assistant", "text": "model 2"} not in result["messages"]
    saved = json.loads((tmp_path / "sessions/live.json").read_text())
    assert saved["conversation"]["messages"] == before["messages"][:before["messages"].index(next(m for m in before["messages"] if m["uuid"] == second.uuid))]
    assert any("model 1" in str(m["content"]) for m in saved["conversation"]["messages"])
    archives = [p for p in (tmp_path / "sessions").glob("*.json") if p.stem != "live"]
    assert json.loads(archives[0].read_text())["conversation"] == before
    if tool:
        assert any(row.get("name") == "Write" for row in result["messages"])
        assert (tmp_path / "created.txt").read_text() == "first"
    if streaming:
        assert any(e.get("type") == "stream_event" for e in emitted)


def test_real_provider_error_survives_rewind_and_resume_without_entering_model_history(tmp_path):
    provider = _RewindProvider(error=True)
    server, emitted = real_turn_session(tmp_path, provider)
    sessions_dir = tmp_path / "sessions"
    error_text = "provider 400: workspace-id required"
    with patch("src.server.agent_server._sessions_dir", return_value=sessions_dir):
        assert server._run_turn("user 1")["subtype"] == "error"
        assert any(e.get("type") == "result" and e.get("error") == error_text for e in emitted)
        assert all(error_text not in str(m.content) for m in server.session.conversation.messages)
        assert server._run_turn("user 2")["subtype"] == "success"
        assert all(error_text not in str(call) for call in provider.calls)
        second = next(m for m in server.session.conversation.messages if m.content == "user 2")
        server._do_rewind("restore", second.uuid, "conversation")
        assert reply(emitted)["messages"] == [
            {"role": "user", "text": "user 1"},
            {"role": "system", "text": f"error: {error_text}"},
        ]
        saved = json.loads((sessions_dir / "live.json").read_text())
        assert len(saved["conversation"]["messages"]) == 1
        assert list(saved["display_messages"]) == [server.session.conversation.messages[0].uuid]
        resumed, resume_emitted = session(tmp_path)
        resumed._do_resume("resume", "live", include_messages=True)
        assert reply(resume_emitted)["messages"] == reply(emitted)["messages"]
        # Rewinding the error's own prompt removes its display record too.
        first_id = resumed.session.conversation.messages[0].uuid
        resumed._do_rewind("first", first_id, "conversation")
        assert reply(resume_emitted)["messages"] == []
        assert json.loads((sessions_dir / "live.json").read_text())["display_messages"] == {}


class _PartialFailureProvider(_RewindProvider):
    def chat_stream_response(self, messages, tools=None, on_text_chunk=None, **kwargs):
        if len(self.calls) == 1:
            self.calls.append(messages)
            if on_text_chunk:
                on_text_chunk("unfinished ")
                on_text_chunk("model output")
            raise ValueError("stream interrupted by provider")
        return super().chat_stream_response(messages, tools=tools, on_text_chunk=on_text_chunk, **kwargs)


def test_partial_response_and_tool_before_error_keep_order_in_rewind_archive_and_resume(tmp_path):
    provider = _PartialFailureProvider(streaming=True, tool=True)
    provider.file_path = str(tmp_path / "created.txt")
    server, emitted = real_turn_session(tmp_path, provider)
    sessions_dir = tmp_path / "sessions"
    with patch("src.server.agent_server._sessions_dir", return_value=sessions_dir):
        assert server._run_turn("user 1")["subtype"] == "error"
        assert server._run_turn("user 2")["subtype"] == "success"
        assert all("unfinished model output" not in str(call) for call in provider.calls)
        assert all("stream interrupted by provider" not in str(call) for call in provider.calls)
        second = next(m for m in server.session.conversation.messages if m.content == "user 2")
        server._do_rewind("restore", second.uuid, "conversation")
        rows = reply(emitted)["messages"]
        assert rows == [
            {"role": "user", "text": "user 1"},
            {"role": "assistant", "text": "model 1 before tool"},
            {"role": "tool", "name": "Write", "input": {"file_path": provider.file_path}},
            {"role": "assistant", "text": "unfinished model output"},
            {"role": "system", "text": "error: stream interrupted by provider"},
        ]
        archive = next(p for p in sessions_dir.glob("*.json") if p.stem != "live")
        saved_original = json.loads(archive.read_text())
        assert saved_original["display_messages"] == server._display_messages
        assert any(m["content"] == "user 2" for m in saved_original["conversation"]["messages"])
        resumed, resume_emitted = session(tmp_path)
        resumed._do_resume("resume", "live", include_messages=True)
        assert reply(resume_emitted)["messages"] == rows
        assert all("unfinished model output" not in str(m.content) for m in resumed.session.conversation.messages)


def test_rewind_removes_only_selected_and_later_display_errors_and_clear_resets_map(tmp_path):
    class _AlwaysErrorProvider(_RewindProvider):
        def chat(self, messages, tools=None, **kwargs):
            self.calls.append(messages)
            raise ValueError(f"provider error {len(self.calls)}")

    server, emitted = real_turn_session(tmp_path, _AlwaysErrorProvider())
    sessions_dir = tmp_path / "sessions"
    with patch("src.server.agent_server._sessions_dir", return_value=sessions_dir):
        server._run_turn("user 1")
        first_id = server.session.conversation.messages[0].uuid
        server._run_turn("user 2")
        second_id = server.session.conversation.messages[1].uuid
        server._do_rewind("restore", second_id, "conversation")
        assert reply(emitted)["messages"] == [
            {"role": "user", "text": "user 1"},
            {"role": "system", "text": "error: provider error 1"},
        ]
        assert set(server._display_messages) == {first_id}
        archive = next(p for p in sessions_dir.glob("*.json") if p.stem != "live")
        assert set(json.loads(archive.read_text())["display_messages"]) == {first_id, second_id}
        asyncio.run(server._handle_control_request({"request_id": "clear", "request": {"subtype": "clear"}}))
        assert reply(emitted)["ok"]
        assert server._display_messages == {}
        assert server.session.conversation.messages == []


def test_resume_without_display_messages_replaces_prior_display_errors(tmp_path):
    server, emitted = session(tmp_path)
    server._display_messages = {"old": [{"role": "system", "text": "old error"}]}
    conv = Conversation()
    conv.add_user_message("different conversation")
    (tmp_path / "other.json").write_text(json.dumps({"conversation": conv.to_dict()}))
    with patch("src.server.agent_server._sessions_dir", return_value=tmp_path):
        server._do_resume("resume", "other", include_messages=True)
    assert reply(emitted)["ok"]
    assert server._display_messages == {}
    assert reply(emitted)["messages"] == [{"role": "user", "text": "different conversation"}]
