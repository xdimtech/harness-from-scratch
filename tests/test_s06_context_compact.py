from pathlib import Path
from types import SimpleNamespace

from agents import s06_context_compact as s06


def tool_use(tool_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=name)


def test_micro_compact_replaces_old_verbose_results_but_preserves_read_file() -> None:
    messages = [
        {"role": "assistant", "content": [tool_use("t1", "bash")]},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "x" * 150,
                }
            ],
        },
        {"role": "assistant", "content": [tool_use("t2", "read_file")]},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t2",
                    "content": "read file body " * 20,
                }
            ],
        },
        {"role": "assistant", "content": [tool_use("t3", "bash")]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t3", "content": "a" * 150}]},
        {"role": "assistant", "content": [tool_use("t4", "write_file")]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t4", "content": "b" * 150}]},
        {"role": "assistant", "content": [tool_use("t5", "bash")]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t5", "content": "c" * 150}]},
    ]

    s06.micro_compact(messages)

    assert messages[1]["content"][0]["content"] == "[Previous: used bash]"
    assert messages[3]["content"][0]["content"] == "read file body " * 20
    assert messages[5]["content"][0]["content"] == "a" * 150


def test_auto_compact_saves_transcript_and_returns_summary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(s06, "TRANSCRIPT_DIR", tmp_path)
    monkeypatch.setattr(s06.time, "time", lambda: 1234567890)

    class StubMessagesAPI:
        def create(self, **kwargs):
            return SimpleNamespace(
                content=[SimpleNamespace(text="Compressed continuity summary.")]
            )

    monkeypatch.setattr(s06, "client", SimpleNamespace(messages=StubMessagesAPI()))

    result = s06.auto_compact(
        [{"role": "user", "content": "Long conversation"}],
        focus="current files",
        reason="manual",
    )

    transcript_path = tmp_path / "transcript_1234567890.jsonl"
    assert transcript_path.exists()
    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert "Compressed continuity summary." in result[0]["content"]
    assert str(transcript_path) in result[0]["content"]
