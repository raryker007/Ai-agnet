from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

_MEMORY_FILE = Path(__file__).parent.parent / "memory.json"
_MAX_INTENTS = 10


class SessionMemory:
    def __init__(self, memory_path: Path = _MEMORY_FILE) -> None:
        self.memory_path = memory_path
        self.user_intents: list[str] = []
        self.files_touched: list[str] = []
        self.facts: dict[str, str] = {}
        self.last_session: str | None = None
        self.loaded: bool = False

    def load_session(self) -> bool:
        if not self.memory_path.exists():
            return False
        try:
            raw = self.memory_path.read_text(encoding="utf-8")
            data: dict = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            return False

        self.user_intents = data.get("user_intents", [])[-_MAX_INTENTS:]
        self.files_touched = data.get("files_touched", [])
        self.facts = data.get("facts", {})
        self.last_session = data.get("last_session")
        self.loaded = True
        return True

    def save_session(
        self,
        new_intents: list[str] | None = None,
        new_files: list[str] | None = None,
    ) -> None:
        combined_intents = (self.user_intents + (new_intents or []))[-_MAX_INTENTS:]

        seen: set[str] = set()
        combined_files: list[str] = []
        for f in self.files_touched + (new_files or []):
            if f not in seen:
                seen.add(f)
                combined_files.append(f)

        payload = {
            "last_session": datetime.now().isoformat(timespec="seconds"),
            "user_intents": combined_intents,
            "files_touched": combined_files,
            "facts": self.facts,
        }
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        self.memory_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def add_fact(self, key: str, value: str) -> None:
        self.facts[key] = value

    def context_summary(self) -> str:
        if not self.loaded:
            return ""
        lines: list[str] = []
        if self.last_session:
            lines.append(f"Last session: {self.last_session}")
        if self.user_intents:
            lines.append(f"Recent user intents: {'; '.join(self.user_intents[-3:])}")
        if self.files_touched:
            lines.append(f"Previously touched files: {', '.join(self.files_touched[-5:])}")
        if self.facts:
            facts_str = "; ".join(f"{k}={v}" for k, v in list(self.facts.items())[:5])
            lines.append(f"Known facts: {facts_str}")
        return "\n".join(lines)
