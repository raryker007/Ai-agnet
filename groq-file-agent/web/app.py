from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from agent.core import FileAgent  # noqa: E402

app = Flask(__name__, static_folder=str(Path(__file__).parent), static_url_path="")
CORS(app)

_sessions: dict[str, FileAgent] = {}

_WORKING_DIR = os.environ.get("WORKING_DIRECTORY", str(Path.cwd()))
_API_KEY = os.environ.get("GROQ_API_KEY", "")


def _get_agent(session_id: str) -> FileAgent:
    if session_id not in _sessions:
        _sessions[session_id] = FileAgent(api_key=_API_KEY, working_directory=_WORKING_DIR)
    return _sessions[session_id]


@app.route("/")
def index():
    return send_from_directory(str(Path(__file__).parent), "index.html")


@app.route("/chat", methods=["POST"])
def chat():
    body = request.get_json(force=True, silent=True) or {}
    message = (body.get("message") or "").strip()
    session_id = (body.get("session_id") or "default").strip()

    if not message:
        return jsonify({"error": "message is required"}), 400
    if not _API_KEY:
        return jsonify({"error": "GROQ_API_KEY not configured"}), 500

    agent = _get_agent(session_id)
    try:
        response = agent.run(message)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    tools_used = [
        entry["operation"]
        for entry in agent.operation_log
        if entry.get("operation")
    ]

    return jsonify({
        "response": response,
        "tools_used": tools_used,
        "gemini_used": agent.last_turn_used_gemini,
    })


@app.route("/files", methods=["GET"])
def list_files():
    session_id = request.args.get("session_id", "default")
    agent = _get_agent(session_id)
    try:
        entries = []
        for p in sorted(Path(agent.working_directory).iterdir()):
            entries.append({
                "name": p.name,
                "is_dir": p.is_dir(),
                "size": p.stat().st_size if p.is_file() else None,
            })
        return jsonify({"files": entries, "working_directory": str(agent.working_directory)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/history", methods=["GET"])
def get_history():
    session_id = request.args.get("session_id", "default")
    agent = _get_agent(session_id)
    return jsonify({"history": agent.get_history()})


@app.route("/clear", methods=["POST"])
def clear_session():
    body = request.get_json(force=True, silent=True) or {}
    session_id = (body.get("session_id") or "default").strip()
    if session_id in _sessions:
        _sessions[session_id].clear_history()
    return jsonify({"status": "cleared", "session_id": session_id})


if __name__ == "__main__":
    if not _API_KEY:
        print("WARNING: GROQ_API_KEY is not set. Set it in .env or environment.")
    app.run(host="0.0.0.0", port=5000, debug=False)
