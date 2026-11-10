"""APIを呼ばず、時系列・履歴・失敗時のログを確かめる。"""

import asyncio
import json

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from app.execution_trace import ExecutionTrace, write_readable_trace


def test_parallel_tools_and_second_model_input_are_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-never-write")

    async def scenario():
        entered = 0
        both_entered = asyncio.Event()

        async def search(query: str) -> str:
            """用語を検索する。"""
            nonlocal entered
            entered += 1
            if entered == 2:
                both_entered.set()
            await asyncio.wait_for(both_entered.wait(), timeout=2)
            return f"{query}の定義"

        def respond(messages, info):
            if any(isinstance(p, ToolReturnPart) for m in messages for p in m.parts):
                return ModelResponse(parts=[TextPart("比較した回答")])
            return ModelResponse(parts=[
                ToolCallPart("search", {"query": "LoRA"}, tool_call_id="call-1"),
                ToolCallPart("search", {"query": "QLoRA"}, tool_call_id="call-2"),
            ])

        agent = Agent(FunctionModel(respond), tools=[search], instructions="用語を比較する")
        trace = ExecutionTrace(tmp_path)
        result = await agent.run("test-secret-never-write", capabilities=[trace])
        trace.finish(result=result)
        assert result.output == "比較した回答"
        return trace.directory

    directory = asyncio.run(scenario())
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    kinds = [e["event"] for e in events]
    starts = [i for i, kind in enumerate(kinds) if kind == "tool.start"]
    ends = [i for i, kind in enumerate(kinds) if kind == "tool.end"]
    assert max(starts) < min(ends)  # 実際に重なった実行を、逐次実行として表示しない。
    assert kinds.count("llm.start") == kinds.count("llm.end") == 2
    assert kinds[-1] == "run.end"
    assert all(e["duration_ms"] >= 0 for e in events if "duration_ms" in e)
    second = json.loads((directory / "llm-002-request.json").read_text())
    results = [p for m in second["messages"] for p in m["parts"] if p["part_kind"] == "tool-return"]
    assert {p["tool_call_id"] for p in results} == {"call-1", "call-2"}
    first = json.loads((directory / "llm-001-request.json").read_text())
    assert first["instructions"][0]["content"] == "用語を比較する"
    assert first["tools"][0]["name"] == "search"
    report = (directory / "trace.md").read_text()
    assert "用語を比較する" in report
    assert '"query": "LoRA"' in report
    assert "LoRAの定義" in report and "QLoRAの定義" in report
    assert "比較した回答" in report
    assert "llm-002" in report and "所要時間：" in report
    assert write_readable_trace(directory).read_text() == report  # 再生成でAPI呼び出しや追記をしない。
    assert all("test-secret-never-write" not in f.read_text() for f in directory.iterdir())


def test_model_failure_keeps_partial_trace(tmp_path):
    def fail(messages, info):
        raise RuntimeError("API失敗の代わり")

    agent = Agent(FunctionModel(fail))
    trace = ExecutionTrace(tmp_path)
    with pytest.raises(RuntimeError) as caught:
        agent.run_sync("質問", capabilities=[trace])
    trace.finish(error=caught.value)
    events = [json.loads(line) for line in (trace.directory / "events.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events] == ["run.start", "llm.start", "llm.error", "run.error"]
    assert json.loads((trace.directory / "summary.json").read_text())["error_type"] == "RuntimeError"
    assert "RuntimeError" in (trace.directory / "trace.md").read_text()


def test_readable_trace_expands_json_string_return_value(tmp_path):
    trace = ExecutionTrace(tmp_path)
    trace._save("tool-001-output.json", json.dumps([{"definition": "学習の説明"}], ensure_ascii=False))
    trace._event("tool.end", "tool-001", name="search", duration_ms=1,
                 output="tool-001-output.json")
    report = write_readable_trace(trace.directory).read_text()
    assert '"definition": "学習の説明"' in report
    assert '\\"definition\\"' not in report
