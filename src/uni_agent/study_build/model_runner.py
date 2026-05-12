from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

from ..storage import ROOT, env_with_dotenv, write_json


def run_optional_model(
    *,
    command_env: str,
    packet: dict[str, Any],
    packet_path: Path,
    response_path: Path,
    transcript_path: Path,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    env = env_with_dotenv()
    command_template = env.get(command_env, "").strip()
    write_json(packet_path, packet)
    if not command_template:
        write_json(
            transcript_path,
            {
                "command": None,
                "returncode": None,
                "reason": "model-command-not-configured",
                "response_path": str(response_path),
            },
        )
        return {"ok": False, "reason": "model-command-not-configured"}
    command = command_template.format(packet=str(packet_path), output=str(response_path), root=str(ROOT))
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env={
            **env,
            "STUDY_BUILD_PACKET_PATH": str(packet_path),
            "STUDY_BUILD_OUTPUT_PATH": str(response_path),
        },
        shell=True,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )
    transcript = {
        "command": shlex.split(command) if command else command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "response_path": str(response_path),
    }
    write_json(transcript_path, transcript)
    response_text = response_path.read_text(encoding="utf-8").strip() if response_path.exists() else completed.stdout.strip()
    try:
        parsed = json.loads(response_text) if response_text else None
    except json.JSONDecodeError as exc:
        return {"ok": False, "reason": "invalid-json", "error": str(exc), "transcript": transcript}
    if completed.returncode != 0:
        return {"ok": False, "reason": "nonzero-exit", "parsed": parsed, "transcript": transcript}
    if not isinstance(parsed, dict):
        return {"ok": False, "reason": "non-object-response", "parsed": parsed, "transcript": transcript}
    write_json(response_path, parsed)
    return {"ok": True, "reason": "model-response", "parsed": parsed, "transcript": transcript}
