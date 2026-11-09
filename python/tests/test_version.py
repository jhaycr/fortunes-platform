import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_python_and_javascript_halves_carry_one_version():
    py = tomllib.loads((ROOT / "python" / "pyproject.toml").read_text())["project"]["version"]
    js = json.loads((ROOT / "package.json").read_text())["version"]
    assert py == js
