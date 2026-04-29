from __future__ import annotations

import json
from pathlib import Path

from .browser import AgentBrowser, AgentBrowserError
from .storage import ROOT, require_env


def load_selectors() -> dict:
    return json.loads((ROOT / "config" / "moodle.selectors.json").read_text(encoding="utf-8"))


def login() -> dict:
    env = require_env(["MOODLE_DASHBOARD_URL", "MOODLE_USERNAME", "MOODLE_PASSWORD"])
    browser = AgentBrowser()
    browser.open(env["MOODLE_DASHBOARD_URL"])
    browser.wait_load()

    selectors = load_selectors()["login"]
    current_url = browser.get_url()
    snapshot = browser.snapshot(interactive=True)
    login_needed = any(token in snapshot.casefold() for token in ["username", "password", "kennwort"])

    if login_needed:
        filled_username = _try_fill(browser, selectors["username_selectors"], env["MOODLE_USERNAME"])
        filled_password = _try_fill(browser, selectors["password_selectors"], env["MOODLE_PASSWORD"])
        if not filled_username or not filled_password:
            raise AgentBrowserError("Could not find Moodle username/password fields.")
        clicked = _try_click(browser, selectors["submit_selectors"])
        if not clicked:
            browser.run(["press", "Enter"], check=False)
        browser.wait_load()

    return {
        "url_before": current_url,
        "url_after": browser.get_url(),
        "title": browser.get_title(),
    }


def _try_fill(browser: AgentBrowser, selectors: list[str], value: str) -> bool:
    for selector in selectors:
        try:
            browser.batch([["fill", selector, value]], check=True)
            return True
        except AgentBrowserError:
            continue
    return False


def _try_click(browser: AgentBrowser, selectors: list[str]) -> bool:
    for selector in selectors:
        try:
            browser.batch([["click", selector]], check=True)
            return True
        except AgentBrowserError:
            continue
    return False


def snapshot(url: str | None = None) -> str:
    env = require_env(["MOODLE_DASHBOARD_URL"])
    browser = AgentBrowser()
    browser.open(url or env["MOODLE_DASHBOARD_URL"])
    browser.wait_load()
    return browser.snapshot(interactive=True)
