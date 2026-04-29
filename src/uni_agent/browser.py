from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .storage import ROOT, env_with_dotenv


class AgentBrowserError(RuntimeError):
    pass


class AgentBrowser:
    def __init__(self, session_name: str = "study-buddy-moodle") -> None:
        self.env = env_with_dotenv()
        self.session_name = session_name
        self.binary = self._resolve_binary()
        profile_dir = ROOT / self.env.get("BROWSER_STATE_DIR", "state/browser") / "profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        self.profile_dir = profile_dir

    def _resolve_binary(self) -> str:
        local = ROOT / "node_modules" / ".bin" / "agent-browser"
        if local.exists():
            return str(local)
        found = shutil.which("agent-browser")
        if found:
            return found
        raise AgentBrowserError(
            "agent-browser is not installed. Run `npm install` and `npm run browser:install`."
        )

    def _base_command(self) -> list[str]:
        return [
            self.binary,
            "--session-name",
            self.session_name,
            "--profile",
            str(self.profile_dir),
        ]

    def run(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        command = self._base_command() + args
        result = subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            cwd=ROOT,
            env=self.env,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            detail = stderr or stdout or f"exit code {result.returncode}"
            raise AgentBrowserError(detail)
        return result

    def batch(self, commands: list[list[str]], *, check: bool = True) -> str:
        payload = json.dumps(commands)
        result = self.run(["batch", "--json"], input_text=payload, check=check)
        return result.stdout

    def open(self, url: str) -> str:
        return self.run(["open", url]).stdout

    def wait_load(self) -> None:
        self.run(["wait", "--load", "networkidle"], check=False, timeout=60)

    def snapshot(self, interactive: bool = True) -> str:
        args = ["snapshot"]
        if interactive:
            args.append("-i")
        return self.run(args, timeout=120).stdout

    def eval_json(self, js: str) -> Any:
        result = self.run(["eval", js], timeout=120).stdout.strip()
        try:
            parsed = json.loads(result)
            if isinstance(parsed, str) and parsed[:1] in "[{":
                return json.loads(parsed)
            return parsed
        except json.JSONDecodeError as exc:
            raise AgentBrowserError(f"agent-browser eval did not return JSON: {result[:500]}") from exc

    def get_url(self) -> str:
        return self.run(["get", "url"]).stdout.strip()

    def get_title(self) -> str:
        return self.run(["get", "title"], check=False).stdout.strip()

    def set_viewport(self, width: int, height: int) -> None:
        self.run(["set", "viewport", str(width), str(height)], check=False)

    def screenshot(self, path: Path, *, full_page: bool = False, selector: str | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        args = ["screenshot"]
        if full_page:
            args.append("--full")
        if selector:
            args.append(selector)
        args.append(str(path))
        self.run(args, check=False)
