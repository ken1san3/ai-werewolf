import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('infra_status_under_test', ROOT / 'scripts/infra_status.py')
infra = importlib.util.module_from_spec(spec)
spec.loader.exec_module(infra)


def test_actual_infrastructure_approval_is_bound():
    assert infra.check_gate(ROOT)[0]


def test_missing_gate_fails_closed(tmp_path):
    assert not infra.check_gate(tmp_path)[0]


def test_changed_design_invalidates_approval(tmp_path):
    metadata = json.loads((ROOT / 'Docs/ai/infra/runner.json').read_text(encoding='utf-8'))
    for key in ('design', 'review'):
        path = tmp_path / metadata[key]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / metadata[key]).read_bytes())
    path = tmp_path / 'Docs/ai/infra/runner.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(metadata), encoding='utf-8')
    assert infra.check_gate(tmp_path)[0]
    (tmp_path / metadata['design']).write_text('changed', encoding='utf-8')
    assert not infra.check_gate(tmp_path)[0]
