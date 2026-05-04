"""
Structural validation for repo-level packaging/CI artifacts.

Covers files added in the Docker + GitHub Actions change:
  - Dockerfile
  - docker-compose.yml
  - .dockerignore
  - .github/workflows/ci.yml
  - requirements-dev.txt
  - mypy.ini

These configs have no Python branches to unit-test, so we assert structural
invariants instead: required values present, no regressions on the deliberate
choices documented inline (non-root user, Python 3.11 base, no Playwright,
data volume mount, ruff + mypy in CI, ignore_missing_imports for APScheduler).
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Dockerfile
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dockerfile_text() -> str:
    p = REPO_ROOT / "Dockerfile"
    assert p.is_file(), "Dockerfile missing at repo root"
    return p.read_text(encoding="utf-8")


def test_dockerfile_uses_python_3_11_slim_base(dockerfile_text: str) -> None:
    assert re.search(r"^FROM\s+python:3\.11-slim\b", dockerfile_text, re.MULTILINE), (
        "Base image must be python:3.11-slim (matches CI's Python version)"
    )


def test_dockerfile_sets_workdir_app(dockerfile_text: str) -> None:
    assert re.search(r"^WORKDIR\s+/app\b", dockerfile_text, re.MULTILINE)


def test_dockerfile_installs_requirements_before_copying_source(
    dockerfile_text: str,
) -> None:
    """Layer-cache invariant: install deps BEFORE copying ``bot/``."""
    pip_install = dockerfile_text.find("pip install")
    copy_bot = dockerfile_text.find("COPY bot/")
    assert pip_install != -1, "Expected `pip install` step in Dockerfile"
    assert copy_bot != -1, "Expected `COPY bot/` step in Dockerfile"
    assert pip_install < copy_bot, (
        "`COPY bot/` must come AFTER `pip install` to keep dep layer cached."
    )


def test_dockerfile_uses_non_root_user(dockerfile_text: str) -> None:
    assert re.search(r"^USER\s+app\b", dockerfile_text, re.MULTILINE), (
        "Container must drop to a non-root user"
    )
    assert re.search(r"useradd[^\n]*\bapp\b", dockerfile_text), (
        "Non-root `app` user must be created via useradd"
    )


def test_dockerfile_creates_data_directory(dockerfile_text: str) -> None:
    """``/app/data`` must exist & be owned by ``app`` for the volume mount."""
    assert re.search(r"mkdir\s+-p\s+/app/data", dockerfile_text)
    assert re.search(r"chown\s+-R\s+app:app\s+/app", dockerfile_text)


def test_dockerfile_cmd_is_module_invocation(dockerfile_text: str) -> None:
    assert re.search(
        r'^CMD\s+\[\s*"python"\s*,\s*"-m"\s*,\s*"bot\.main"\s*\]',
        dockerfile_text,
        re.MULTILINE,
    ), 'CMD must be exec form ["python", "-m", "bot.main"]'


def test_dockerfile_skips_playwright_install(dockerfile_text: str) -> None:
    """Scraper is httpx-only; installing Playwright would just bloat the image."""
    assert "playwright install" not in dockerfile_text.lower(), (
        "Do not install Playwright: bot/scraper/catalog.py is httpx-only "
        "and the playwright import is a soft optional fallback."
    )


# ---------------------------------------------------------------------------
# .dockerignore
# ---------------------------------------------------------------------------


def test_dockerignore_excludes_secrets_and_local_state() -> None:
    p = REPO_ROOT / ".dockerignore"
    assert p.is_file(), ".dockerignore missing at repo root"
    entries = {
        line.strip()
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    must_exclude = {".env", ".git", ".venv", "data", "__pycache__"}
    missing = must_exclude - entries
    assert not missing, f".dockerignore is missing required entries: {sorted(missing)}"


# ---------------------------------------------------------------------------
# docker-compose.yml
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def compose_doc() -> dict[str, Any]:
    p = REPO_ROOT / "docker-compose.yml"
    assert p.is_file(), "docker-compose.yml missing at repo root"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), "docker-compose.yml must be a YAML mapping"
    return doc


def test_compose_has_bot_service(compose_doc: dict[str, Any]) -> None:
    services = compose_doc.get("services")
    assert isinstance(services, dict) and "bot" in services, (
        "docker-compose.yml must define a top-level `bot` service"
    )


def test_compose_bot_service_build_context_is_repo(compose_doc: dict[str, Any]) -> None:
    bot = compose_doc["services"]["bot"]
    assert bot.get("build") == "." or (
        isinstance(bot.get("build"), dict) and bot["build"].get("context") in (".", "./")
    ), "bot.build must point at the repo root"


def test_compose_mounts_data_volume(compose_doc: dict[str, Any]) -> None:
    bot = compose_doc["services"]["bot"]
    volumes = bot.get("volumes") or []
    assert "./data:/app/data" in volumes, (
        "Bind mount `./data:/app/data` is required so the SQLite file persists "
        "across container restarts."
    )


def test_compose_uses_env_file(compose_doc: dict[str, Any]) -> None:
    bot = compose_doc["services"]["bot"]
    env_file = bot.get("env_file")
    if isinstance(env_file, list):
        assert ".env" in env_file
    else:
        assert env_file == ".env", "bot.env_file must be `.env` (never committed)"


def test_compose_restart_policy_is_unless_stopped(compose_doc: dict[str, Any]) -> None:
    bot = compose_doc["services"]["bot"]
    assert bot.get("restart") == "unless-stopped"


# ---------------------------------------------------------------------------
# .github/workflows/ci.yml
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ci_doc() -> dict[Any, Any]:
    # Keys may be bool: PyYAML parses an unquoted `on:` as the boolean True
    # under YAML 1.1, so we type the mapping with Any keys.
    p = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert p.is_file(), "CI workflow missing at .github/workflows/ci.yml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def test_ci_triggers_on_push_and_pr_to_main(ci_doc: dict[Any, Any]) -> None:
    triggers = ci_doc.get("on") or ci_doc.get(True)
    assert isinstance(triggers, dict), (
        "CI must trigger via a mapping (push/pull_request), not a string or list"
    )
    for event in ("push", "pull_request"):
        spec = triggers.get(event)
        assert isinstance(spec, dict), f"`on.{event}` missing or not a mapping"
        branches = spec.get("branches") or []
        assert "main" in branches, f"`on.{event}.branches` must include `main`"


def test_ci_uses_python_3_11(ci_doc: dict[Any, Any]) -> None:
    jobs = ci_doc.get("jobs") or {}
    assert jobs, "CI must define at least one job"
    found_setup_python = False
    for job in jobs.values():
        for step in job.get("steps", []):
            uses = step.get("uses", "")
            if uses.startswith("actions/setup-python@"):
                found_setup_python = True
                version = str(step.get("with", {}).get("python-version", ""))
                assert version == "3.11", (
                    f"setup-python must request 3.11 (matches Dockerfile base), got {version!r}"
                )
    assert found_setup_python, "CI must use actions/setup-python"


def test_ci_runs_ruff_and_mypy(ci_doc: dict[Any, Any]) -> None:
    jobs = ci_doc.get("jobs") or {}
    all_run_steps = " ".join(
        str(step.get("run", "") or "")
        for job in jobs.values()
        for step in job.get("steps", [])
    )
    assert "ruff check" in all_run_steps, "CI must run `ruff check`"
    assert re.search(r"\bmypy\s+bot/?\b", all_run_steps), "CI must run `mypy bot/`"


def test_ci_installs_dev_requirements(ci_doc: dict[Any, Any]) -> None:
    """Dev requirements (which transitively pull runtime deps) must be installed
    so ruff and mypy are available."""
    jobs = ci_doc.get("jobs") or {}
    all_run_steps = " ".join(
        str(step.get("run", "") or "")
        for job in jobs.values()
        for step in job.get("steps", [])
    )
    assert re.search(
        r"pip\s+install[^\n]*requirements-dev\.txt", all_run_steps
    ), "CI must `pip install -r requirements-dev.txt`"


# ---------------------------------------------------------------------------
# requirements-dev.txt
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dev_requirements() -> list[str]:
    p = REPO_ROOT / "requirements-dev.txt"
    assert p.is_file()
    return [
        line.strip()
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


@pytest.mark.parametrize("pkg", ["ruff", "mypy", "pytest", "pytest-asyncio"])
def test_dev_requirements_includes_required_tools(
    dev_requirements: list[str], pkg: str
) -> None:
    pattern = re.compile(rf"^{re.escape(pkg)}(\s|>=|==|<=|~=|<|>|;|$)")
    assert any(pattern.match(line) for line in dev_requirements), (
        f"requirements-dev.txt must declare `{pkg}` (got: {dev_requirements})"
    )


def test_dev_requirements_chains_runtime_requirements(
    dev_requirements: list[str],
) -> None:
    assert any(
        line.replace(" ", "") == "-rrequirements.txt" for line in dev_requirements
    ), "requirements-dev.txt must include `-r requirements.txt`"


# ---------------------------------------------------------------------------
# mypy.ini
# ---------------------------------------------------------------------------


def test_mypy_ini_ignores_missing_imports() -> None:
    """APScheduler ships without type stubs; project policy is to ignore."""
    p = REPO_ROOT / "mypy.ini"
    assert p.is_file(), "mypy.ini missing"
    parser = configparser.ConfigParser()
    parser.read(p, encoding="utf-8")
    assert parser.has_section("mypy"), "mypy.ini must have [mypy] section"
    assert parser.getboolean("mypy", "ignore_missing_imports", fallback=False), (
        "mypy.ini must set `ignore_missing_imports = True` "
        "(otherwise CI fails on apscheduler import-untyped errors)"
    )
    assert parser.get("mypy", "python_version", fallback="") == "3.11", (
        "mypy.ini python_version must match Dockerfile / CI (3.11)"
    )
