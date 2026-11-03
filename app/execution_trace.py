"""第3回: LLMとツールの境界で、実行順・時間・入出力を記録する。"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from time import perf_counter

from pydantic_core import to_jsonable_python
from pydantic_ai.capabilities import AbstractCapability


def write_readable_trace(directory: Path) -> Path:
    """保存済みのイベントと入出力を、時系列のMarkdownへまとめる。APIは呼ばない。"""
    sections = [
        "# 実行トレース\n",
        "LLMの入力はPydantic AIでの表現。API認証情報や生のHTTP通信は含まない。\n",
        "JSON文字列の戻り値は、表示用にJSONとして展開する。原文は個別ファイルに残す。\n",
    ]

    def block(value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                pass
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
        fence = "`" * max(3, longest + 1)
        sections.append(f"{fence}\n{text}\n{fence}\n")

    def parts(messages):
        for index, message in enumerate(messages, 1):
            sections.append(f"#### メッセージ {index}：{message.get('kind', 'response')}\n")
            for part in message.get("parts", []):
                kind = part.get("part_kind", "unknown")
                sections.append(f"**{kind}**\n")
                if kind in ("user-prompt", "system-prompt", "text"):
                    block(part.get("content", ""))
                elif kind == "tool-call":
                    block({k: part[k] for k in ("tool_name", "tool_call_id", "args") if k in part})
                elif kind == "tool-return":
                    block({k: part[k] for k in ("tool_name", "tool_call_id") if k in part})
                    block(part.get("content"))
                else:
                    block(part)

    for index, line in enumerate((directory / "events.jsonl").read_text(encoding="utf-8").splitlines(), 1):
        event = json.loads(line)
        sections.append(
            f"## {index}. +{event['elapsed_ms']:.3f}ms — {event['event']} / {event['span']}\n"
        )
        sections.append(f"時刻：{event['timestamp']}\n")
        if "duration_ms" in event:
            sections.append(f"所要時間：{event['duration_ms']:.3f}ms\n")
        if "error_type" in event:
            sections.append(f"例外：{event['error_type']}\n")
        filename = next((event[k] for k in ("request", "response", "input", "output") if k in event), None)
        if not filename:
            continue
        data = json.loads((directory / filename).read_text(encoding="utf-8"))
        sections.append(f"保存元：[{filename}]({filename})\n")
        if event["event"] == "llm.start":
            sections.append(f"モデル：{data['model']}\n\n### 送信した指示\n")
            for instruction in data.get("instructions") or []:
                block(instruction["content"])
            sections.append("### 送信した会話履歴（この呼び出しに含めた全件）\n")
            parts(data["messages"])
            sections.append("### 利用可能として渡したツール\n")
            for tool in data["tools"]:
                sections.append(f"#### {tool['name']}\n")
                block(tool.get("description", ""))
                block(tool["parameters_json_schema"])
        elif event["event"] == "llm.end":
            sections.append("### LLMが出力した内容\n")
            parts([data])
            sections.append("### 使用量\n")
            block(data.get("usage"))
        elif event["event"] == "tool.start":
            sections.append(f"ツール：{data['name']} / 要求元：{data['parent']}\n")
            sections.append("### 実行した引数\n")
            block(data["arguments"])
            sections.append(f"呼び出しID：{data['tool_call_id']}\n")
        elif event["event"] == "tool.end":
            sections.append(f"ツール：{event['name']}\n\n### 戻り値\n")
            block(data)
    path = directory / "trace.md"
    path.write_text("\n".join(sections), encoding="utf-8")
    return path


class ExecutionTrace(AbstractCapability):
    """1回の実行につき1つ作成し、run_syncのcapabilitiesへ渡す。"""

    def __init__(self, root: Path):
        stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S.%f%z")
        self.directory = root / stamp
        self.directory.mkdir(parents=True, exist_ok=False)
        self.started = perf_counter()
        self.model_calls = 0
        self.tool_calls = 0
        # ログ用にだけ保持し、ファイルや画面へは出力しない。
        self.secret = os.getenv("GEMINI_API_KEY", "")
        self._event("run.start", "run")

    def _json(self, value):
        text = json.dumps(to_jsonable_python(value), ensure_ascii=False, indent=2)
        if self.secret:
            text = text.replace(self.secret, "[REDACTED]")
        return text

    def _save(self, filename, value):
        (self.directory / filename).write_text(self._json(value) + "\n", encoding="utf-8")

    def _event(self, kind, span, **details):
        event = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "elapsed_ms": round((perf_counter() - self.started) * 1000, 3),
            "event": kind,
            "span": span,
            **details,
        }
        # awaitを挟まず追記するため、並行ツールでも開始・終了を実際の順番で記録する。
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(json.loads(self._json(event)), ensure_ascii=False) + "\n")
        line = (
            f"{event['elapsed_ms']:10.3f}ms  {kind:<12} {span}"
            + (f"  {details['name']}" if "name" in details else "")
            + (f"  {details['duration_ms']:.3f}ms" if "duration_ms" in details else "")
        )
        with (self.directory / "timeline.txt").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(line, flush=True)

    async def wrap_model_request(self, ctx, *, request_context, handler):
        self.model_calls += 1
        span = f"llm-{self.model_calls:03d}"
        params = request_context.model_request_parameters
        self._save(f"{span}-request.json", {
            "model": request_context.model.model_name,
            "instructions": params.instruction_parts,
            "messages": request_context.messages,
            "tools": params.function_tools,
        })
        self._event("llm.start", span, request=f"{span}-request.json")
        started = perf_counter()
        try:
            response = await handler(request_context)
        except BaseException as error:
            self._event("llm.error", span, duration_ms=(perf_counter() - started) * 1000,
                        error_type=type(error).__name__)
            raise
        duration = (perf_counter() - started) * 1000
        self._save(f"{span}-response.json", response)
        self._event("llm.end", span, duration_ms=duration, response=f"{span}-response.json")
        return response

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        self.tool_calls += 1
        span = f"tool-{self.tool_calls:03d}"
        parent = f"llm-{self.model_calls:03d}"
        self._save(f"{span}-input.json", {
            "name": call.tool_name, "tool_call_id": call.tool_call_id,
            "parent": parent, "arguments": args,
        })
        self._event("tool.start", span, name=call.tool_name, parent=parent,
                    tool_call_id=call.tool_call_id, input=f"{span}-input.json")
        started = perf_counter()
        try:
            result = await handler(args)
        except BaseException as error:
            self._event("tool.error", span, duration_ms=(perf_counter() - started) * 1000,
                        name=call.tool_name, error_type=type(error).__name__)
            raise
        duration = (perf_counter() - started) * 1000
        self._save(f"{span}-output.json", result)
        self._event("tool.end", span, duration_ms=duration, name=call.tool_name,
                    output=f"{span}-output.json")
        return result

    def finish(self, result=None, error=None):
        self._save("summary.json", {
            "status": "error" if error else "ok",
            "duration_ms": (perf_counter() - self.started) * 1000,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "usage": result.usage if result is not None else None,
            "output": result.output if result is not None else None,
            "error_type": type(error).__name__ if error else None,
        })
        self._event("run.error" if error else "run.end", "run")
        write_readable_trace(self.directory)
