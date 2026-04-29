from pathlib import Path
import py_compile


ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = ROOT / "agents"
AGENT_FILES = sorted(
    path for path in AGENTS_DIR.glob("*.py") if path.name != "__init__.py"
)


def test_agent_scripts_exist() -> None:
    assert AGENT_FILES, "expected at least one agent script"


def test_agent_scripts_compile() -> None:
    for agent_path in AGENT_FILES:
        py_compile.compile(str(agent_path), doraise=True)
