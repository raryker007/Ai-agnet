from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from groq import Groq
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config.settings import DESTRUCTIVE_TOOLS, MAX_TOKENS, PRIMARY_MODEL, TEMPERATURE
from agent.memory import SessionMemory
from agent.tools import ToolResult, execute_tool, get_tool_schemas, set_working_directory

console = Console()

_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "config" / "system_prompt.md"


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
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_api(self, model: str):
        messages = [{"role": "system", "content": self.system_prompt}, *self.conversation_history]
        return self.client.chat.completions.create(
            model=model,
            messages=messages,
            tools=get_tool_schemas(),
            tool_choice="auto",
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
        )

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

    def _execute_tool_call(self, tool_call) -> str:
        name: str = tool_call.function.name

        try:
            args: dict = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError as exc:
            bad = ToolResult(False, error=f"Malformed tool arguments: {exc}")
            return bad.to_json()

        is_destructive = name in DESTRUCTIVE_TOOLS
        prefix = "[bold red]⚠[/bold red]" if is_destructive else "[bold cyan]→[/bold cyan]"
        arg_preview = ", ".join(
            f"{k}={repr(v)[:40]}" for k, v in args.items()
        )
        console.print(f"  {prefix} [bold]{name}[/bold]([dim]{arg_preview}[/dim])")

        result: ToolResult = execute_tool(name, args)
        self._log_operation(name, args, result.success)

        if result.success:
            console.print(f"    [green]✓ ok[/green]")
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
                response = self._call_api(PRIMARY_MODEL)

            message = response.choices[0].message
            self.conversation_history.append(self._serialize_message(message))

            if not message.tool_calls:
                return message.content or ""

            for tc in message.tool_calls:
                result_json = self._execute_tool_call(tc)
                self.conversation_history.append({
                    "role": "tool",
                    "content": result_json,
                    "tool_call_id": tc.id,
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
