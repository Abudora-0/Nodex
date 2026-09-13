import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_dashes.py"


def test_project_has_no_em_dashes():
    spec = importlib.util.spec_from_file_location("check_dashes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.find_dashes() == []
