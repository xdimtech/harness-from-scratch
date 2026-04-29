from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any


def now_ts() -> float:
    return time.time()


def slugify(text: str, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48] or fallback


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")
    tmp.replace(path)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def safe_resolve(base: Path, candidate: str) -> Path:
    path = Path(candidate).expanduser().resolve()
    if not is_relative_to(path, base.resolve()) and path != base.resolve():
        raise ValueError(f"Path escapes workspace: {candidate}")
    return path


def detect_repo_root(start: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        raise ValueError((result.stdout + result.stderr).strip() or f"Not a git repository: {start}")
    root = Path(result.stdout.strip()).resolve()
    if not root.exists():
        raise ValueError(f"Repository root not found: {root}")
    return root


def list_repo_entries(repo_root: Path, limit: int = 80) -> list[str]:
    entries: list[str] = []
    for path in sorted(repo_root.rglob("*")):
        if len(entries) >= limit:
            break
        if any(part.startswith(".") and part not in {".github", ".gitignore"} for part in path.parts[len(repo_root.parts) :]):
            continue
        if path.is_dir():
            continue
        if path.stat().st_size > 128_000:
            continue
        entries.append(str(path.relative_to(repo_root)))
    return entries


def summarize_repo(repo_root: Path) -> dict[str, Any]:
    top_level = sorted(item.name for item in repo_root.iterdir() if item.name != ".git")
    files = list_repo_entries(repo_root, limit=60)
    signals = {
        "python": (repo_root / "pyproject.toml").exists() or (repo_root / "requirements.txt").exists(),
        "node": (repo_root / "package.json").exists(),
        "tests": any("test" in path.lower() for path in files),
        "frontend": any(path.endswith((".tsx", ".jsx", ".vue", ".css", ".html")) for path in files),
        "docs": any(path.lower().startswith("docs/") or path.lower() == "readme.md" for path in files),
    }
    return {
        "repo_root": str(repo_root),
        "top_level": top_level[:24],
        "sample_files": files,
        "signals": signals,
    }
