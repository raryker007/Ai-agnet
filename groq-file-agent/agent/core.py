from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import groq as _groq_module
from groq import Groq
from rich.console import Console
from rich.table import Table

from config.settings import (
    DESTRUCTIVE_TOOLS,
    GEMINI_API_KEY_ENV,
    GEMINI_MODEL,
    MAX_TOKENS,
    PRIMARY_MODEL,
    TEMPERATURE,
)
from agent.memory import SessionMemory
from agent.tools import ToolResult, execute_tool, get_tool_schemas, set_working_directory

console = Console()

_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "config" / "system_prompt.md"

# ---------------------------------------------------------------------------
# Gemini format converters
# ---------------------------------------------------------------------------

_GEMINI_TYPE_MAP: dict[str, str] = {
    "string": "STRING",
    "object": "OBJECT",
    "array": "ARRAY",
    "boolean": "BOOLEAN",
    "number": "NUMBER",
    "integer": "INTEGER",
}


def _convert_schema_types(schema: dict) -> dict:
    result: dict = {}
    if "type" in schema:
        result["type"] = _GEMINI_TYPE_MAP.get(schema["type"], schema["type"].upper())
    if "description" in schema:
        result["description"] = schema["description"]
    if "properties" in schema:
        result["properties"] = {k: _convert_schema_types(v) for k, v in schema["properties"].items()}
    if "required" in schema:
        result["required"] = schema["required"]
    if "items" in schema:
        result["items"] = _convert_schema_types(schema["items"])
    return result


def _schemas_to_gemini(tool_schemas: list[dict]) -> list[dict]:
    """Convert OpenAI-style tool schemas to Gemini function_declarations format."""
    declarations = []
    for tool in tool_schemas:
        fn = tool["function"]
        decl: dict = {"name": fn["name"], "description": fn["description"]}
        if "parameters" in fn:
            decl["parameters"] = _convert_schema_types(fn["parameters"])
        declarations.append(decl)
    return [{"function_declarations": declarations}]


def _history_to_gemini(history: list[dict]) -> list[dict]:
    """Convert OpenAI-format conversation history to Gemini contents format.

    Key differences handled:
    - assistant → model
    - tool_calls parts become functionCall parts inside the model turn
    - consecutive role=tool messages are grouped into one user turn with
      functionResponse parts (Gemini requires this grouping)
    """
    call_id_to_name: dict[str, str] = {}
    for msg in history:
        for tc in msg.get("tool_calls") or []:
            call_id_to_name[tc["id"]] = tc["function"]["name"]

    contents: list[dict] = []
    i = 0
    while i < len(history):
        msg = history[i]
        role = msg.get("role")

        if role == "user":
            contents.append({"role": "user", "parts": [{"text": msg.get("content") or ""}]})
            i += 1

        elif role == "assistant":
            parts: list[dict] = []
            if msg.get("content"):
                parts.append({"text": msg["content"]})
            for tc in msg.get("tool_calls") or []:
                parts.append({"functionCall": {
                    "name": tc["function"]["name"],
                    "args": json.loads(tc["function"]["arguments"]),
                }})
            if parts:
                contents.append({"role": "model", "parts": parts})
            i += 1

        elif role == "tool":
            fn_parts: list[dict] = []
            while i < len(history) and history[i].get("role") == "tool":
                t = history[i]
                name = call_id_to_name.get(t.get("tool_call_id", ""), "unknown_function")
                try:
                    response_data = json.loads(t.get("content") or "{}")
                except (json.JSONDecodeError, TypeError):
                    response_data = {"raw": t.get("content", "")}
                fn_parts.append({"functionResponse": {"name": name, "response": response_data}})
                i += 1
            contents.append({"role": "user", "parts": fn_parts})

        else:
            i += 1

    return contents


def _normalize_gemini_response(data: dict) -> dict[str, Any]:
    """Convert a Gemini generateContent response to the normalized message dict
    used throughout this codebase (same shape as _serialize_message output)."""
    candidate = data.get("candidates", [{}])[0]
    parts = candidate.get("content", {}).get("parts", [])

    text_parts = [p["text"] for p in parts if "text" in p]
    content: str | None = "\n".join(text_parts) if text_parts else None

    tool_calls = []
    for idx, p in enumerate(parts):
        if "functionCall" in p:
            fc = p["functionCall"]
            tool_calls.append({
                "id": f"gcall_{idx}",
                "type": "function",
                "function": {
                    "name": fc["name"],
                    "arguments": json.dumps(fc.get("args", {})),
                },
            })

    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class FileAgent:
    def __init__(self, api_key: str, working_directory: Optional[str] = None) -> None:
        self.client = Groq(api_key=api_key)
        self.conversation_history: list[dict[str, Any]] = []
        self.operation_log: list[dict[str, Any]] = []
        self.system_prompt = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

        work_dir = Path(working_directory).resolve() if working_directory else Path.cwd()
        set_working_directory(work_dir)
        self.working_directory = work_dir

        self.memory = SessionMemory()
        if self.memory.load_session():
            ctx = self.memory.context_summary()
            if ctx:
                self.system_prompt += f"\n\n## Previous Session Context\n{ctx}"
            console.print("[dim cyan]Resuming previous session...[/dim cyan]")

    # ------------------------------------------------------------------
    # API calls
    # ------------------------------------------------------------------

    def _call_groq(self) -> dict[str, Any]:
        messages = [{"role": "system", "content": self.system_prompt}, *self.conversation_history]
        resp = self.client.chat.completions.create(
            model=PRIMARY_MODEL,
            messages=messages,
            tools=get_tool_schemas(),
            tool_choice="auto",
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
        )
        return self._serialize_message(resp.choices[0].message)

    def _call_gemini(self) -> dict[str, Any]:
        import requests  # noqa: PLC0415
        api_key = os.environ.get(GEMINI_API_KEY_ENV, "")
        if not api_key:
            raise RuntimeError(
                f"Groq rate-limited and {GEMINI_API_KEY_ENV} is not set — cannot fall back to Gemini"
            )
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{GEMINI_MODEL}:generateContent?key={api_key}"
        )
        body = {
            "system_instruction": {"parts": [{"text": self.system_prompt}]},
            "contents": _history_to_gemini(self.conversation_history),
            "tools": _schemas_to_gemini(get_tool_schemas()),
            "generationConfig": {"maxOutputTokens": MAX_TOKENS, "temperature": TEMPERATURE},
            "tool_config": {"function_calling_config": {"mode": "AUTO"}},
        }
        resp = requests.post(url, json=body, timeout=60)
        resp.raise_for_status()
        return _normalize_gemini_response(resp.json())

    def _call_primary_turn(self) -> dict[str, Any]:
        try:
            return self._call_groq()
        except _groq_module.RateLimitError:
            console.print("[yellow]Switching to Gemini 2.5 Flash...[/yellow]")
            return self._call_gemini()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_message(message) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]
        return msg

    def _log_operation(self, name: str, args: dict, success: bool) -> None:
        self.operation_log.append({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "operation": name,
            "args": args,
            "success": success,
        })

    def _execute_tool_call(self, tc: dict) -> str:
        name: str = tc["function"]["name"]

        try:
            args: dict = json.loads(tc["function"]["arguments"])
        except json.JSONDecodeError as exc:
            bad = ToolResult(False, error=f"Malformed tool arguments: {exc}")
            return bad.to_json()

        is_destructive = name in DESTRUCTIVE_TOOLS
        prefix = "[bold red]⚠[/bold red]" if is_destructive else "[bold cyan]→[/bold cyan]"
        arg_preview = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items())
        console.print(f"  {prefix} [bold]{name}[/bold]([dim]{arg_preview}[/dim])")

        result: ToolResult = execute_tool(name, args)
        self._log_operation(name, args, result.success)

        if result.success:
            console.print("    [green]✓ ok[/green]")
        else:
            console.print(f"    [red]✗ {result.error}[/red]")

        return result.to_json()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self, user_message: str) -> str:
        self.conversation_history.append({"role": "user", "content": user_message})

        while True:
            with console.status("[bold yellow]Thinking...[/bold yellow]", spinner="dots"):
                msg = self._call_primary_turn()

            self.conversation_history.append(msg)

            if not msg.get("tool_calls"):
                return msg.get("content") or ""

            for tc in msg["tool_calls"]:
                result_json = self._execute_tool_call(tc)
                self.conversation_history.append({
                    "role": "tool",
                    "content": result_json,
                    "tool_call_id": tc["id"],
                })

    def close(self) -> None:
        intents = [
            m["content"]
            for m in self.conversation_history
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ]
        files = list({
            str(v)
            for entry in self.operation_log
            for v in entry["args"].values()
            if isinstance(v, str) and v and ("/" in v or "." in v)
        })
        self.memory.save_session(new_intents=intents, new_files=files)

    def display_operation_log(self) -> None:
        if not self.operation_log:
            console.print("[dim]No operations recorded this session.[/dim]")
            return

        table = Table(title="Session Operation Log", show_header=True, header_style="bold magenta")
        table.add_column("Time", style="dim", width=10)
        table.add_column("Operation", style="cyan", width=20)
        table.add_column("Key Args", style="white")
        table.add_column("Status", justify="center", width=8)

        for entry in self.operation_log:
            key_arg = next(iter(entry["args"].values()), "") if entry["args"] else ""
            preview = repr(key_arg)[:48]
            status = "[green]✓[/green]" if entry["success"] else "[red]✗[/red]"
            table.add_row(
                entry["timestamp"].split("T")[1],
                entry["operation"],
                preview,
                status,
            )

        console.print(table)
