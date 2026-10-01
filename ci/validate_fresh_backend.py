#!/usr/bin/env python3
"""Fresh-site AOS repository contract. Pure stdlib; suitable for pre-commit and CI."""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEATURES = (
    "localization", "authentication", "media", "accounts", "notifications",
    "verification", "social", "maps", "sellers", "catalog", "ads",
    "search-ranking", "saved-search", "wishlist", "reviews", "video-processing",
    "shorts", "livekit", "live", "calls", "chat", "activity", "reports",
    "moderation", "analytics", "diagnostics",
)
# Assemble disallowed prose to avoid self-matching in a repository-wide grep.
DISALLOWED = ("le" + "gacy", "backward" + " compatibility", "backwards" + " compatibility")
TEXT_SUFFIXES = frozenset({
    ".py", ".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".sh",
    ".js", ".ts", ".conf", ".env", ".example", ".service", ".timer",
    ".template", ".html", ".ini", ".lock", ".in", ".gitignore",
})
SKIP_DIRS = frozenset({".git", ".venv", ".mypy_cache", ".pytest_cache", "__pycache__", "node_modules"})
RETIRED_PATCHES = frozenset({
    "add_unique_constraints", "harden_live_subsystem", "canonicalize_live_runtime",
    "migrate_chat_to_group_model", "harden_chat_translation_cache",
    "migrate_calls_to_conference_model", "finalize_reports_domain", "finalize_activity_read_model",
})
ERRORS: list[str] = []


def require(value: bool, message: str) -> None:
    if not value:
        ERRORS.append(message)


def main() -> int:
    docs = ROOT / "docs/features"
    actual = {path.name for path in docs.iterdir() if path.is_dir()}
    require(len(FEATURES) == 26 and len(set(FEATURES)) == 26, "incorrect authoritative feature manifest")
    require(actual == set(FEATURES), f"feature directory mismatch: missing={sorted(set(FEATURES)-actual)} extra={sorted(actual-set(FEATURES))}")
    topic_markers = {
        "architecture": ("architecture",),
        "ownership": ("responsib", "ownership", "boundar"),
        "api": ("## api", "## public api", "endpoint inventory"),
        "data": ("data model", "doctype", "durable model", "persistent model"),
        "lifecycle": ("lifecycle", "state transition", "terminal state"),
        "dependencies": ("dependenc",),
        "security": ("security", "privacy", "authorization"),
        "concurrency": ("concurren", "transaction"),
        "scale": ("scalab", "scaling", "performance", "capacity"),
        "tests": ("test",),
    }
    for feature in FEATURES:
        folder = docs / feature
        require(sorted(path.name for path in folder.glob("*.md")) == ["README.md"], f"{feature} must own exactly README.md")
        readme = folder / "README.md"
        require(readme.is_file(), f"{feature} README missing")
        require(f"({feature}/README.md)" in (docs / "README.md").read_text(), f"missing feature index link: {feature}")
        if readme.is_file():
            content = readme.read_text(encoding="utf-8").casefold()
            for topic, markers in topic_markers.items():
                require(any(marker in content for marker in markers), f"{feature} missing {topic} documentation")

    for path in ROOT.rglob("*"):
        if not path.is_file() or SKIP_DIRS.intersection(path.relative_to(ROOT).parts):
            continue
        relative = path.relative_to(ROOT).as_posix()
        require(path.stat().st_size > 0, f"empty repository artifact: {relative}")
        if path.suffix not in TEXT_SUFFIXES and path.name not in {"Makefile", "Dockerfile", ".eslintrc", ".editorconfig", ".pre-commit-config.yaml"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for phrase in DISALLOWED:
            if phrase.casefold() in content.casefold():
                ERRORS.append(f"retired contract phrase in {relative}: {phrase[:2]}***")

    # Backup scripts execute these CLI modules directly: their executable bits
    # must survive packaging, not merely their source contents.
    for utility in ("backup_crypto.py", "backup_artifacts.py"):
        cli = ROOT / "infra/backup" / utility
        require(cli.is_file() and bool(cli.stat().st_mode & 0o111), f"backup CLI not executable: {utility}")

    # Reject deprecated pagination keyword arguments throughout active backend code.
    pagination_keywords = {"start", "page_length", "limit_start", "limit_page_length"}
    for path in (ROOT / "aos").rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"get_all", "get_list"}:
                forbidden = {keyword.arg for keyword in node.keywords} & pagination_keywords
                require(not forbidden, f"outdated pagination keywords in {path.relative_to(ROOT)}:{node.lineno}: {sorted(forbidden)}")

    patches = ROOT / "aos/patches.txt"
    entries = [line.strip() for line in patches.read_text().splitlines() if line.strip() and not line.lstrip().startswith(("#", "["))]
    names = [entry.removeprefix("aos.patches.v1_0.") for entry in entries]
    require(len(names) == len(set(names)), "duplicate patch registration")
    require(not set(names).intersection(RETIRED_PATCHES), "historical data-conversion patch still registered")
    require(all(entry.startswith("aos.patches.v1_0.install_") or entry == "aos.patches.v1_0.add_outbox_indexes" for entry in entries), "registered patches must be schema installers")
    for name in RETIRED_PATCHES:
        require(not (ROOT / f"aos/patches/v1_0/{name}.py").exists(), f"obsolete data-conversion module exists: {name}")
    migrate = (ROOT / "aos/migrate.py").read_text()
    for name in names:
        require(f"{name}.execute" in migrate, f"registered schema installer not reasserted after model sync: {name}")
    for name in names:
        tree = ast.parse((ROOT / f"aos/patches/v1_0/{name}.py").read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr not in {"sql", "sql_ddl"} or not node.args:
                continue
            expr = node.args[0]
            if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
                raw = expr.value
            elif isinstance(expr, ast.JoinedStr):
                raw = "".join(value.value for value in expr.values if isinstance(value, ast.Constant) and isinstance(value.value, str))
            else:
                continue
            if re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|TRUNCATE)\b", raw, re.I):
                ERRORS.append(f"schema installer {name} includes business-data mutation at line {node.lineno}")

    for relative in (
        "aos/api/v1/video_processing/__init__.py",
        "aos/api/v1/shorts/toggle_like.py",
        "aos/api/v1/shorts/toggle_save_short.py",
        "aos/api/v1/shorts/feed_by_ad.py",
    ):
        require(not (ROOT / relative).exists(), f"forbidden obsolete client route: {relative}")

    short = (ROOT / "aos/patches/v1_0/install_shorts_indexes.py").read_text()
    live = (ROOT / "aos/patches/v1_0/install_live_indexes.py").read_text()
    require('"uq_short_view_identity_day", ("short", "view_date", "identity_key"), True' in short, "Short view identity/day database uniqueness missing")
    require('"uq_live_active_session", ("live_stream", "active_identity_key"), True' in live, "Live active session database uniqueness missing")
    if ERRORS:
        for error in ERRORS:
            print("FAIL:", error, file=sys.stderr)
        return 1
    print("Fresh-site repository invariants: OK (26 feature READMEs, source phrases, schema installers, key uniqueness)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
