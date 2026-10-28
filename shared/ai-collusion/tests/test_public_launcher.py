"""Exercise the public launcher end to end with stub models and real task inputs."""
import json
from pathlib import Path
import pytest
from scripts import run_wiki


def workspace(tmp_path, monkeypatch):
    source = Path(run_wiki.ROOT)
    for name in ('data', 'wikitasks'):
        (tmp_path / name).symlink_to(source / name, target_is_directory=True)
    monkeypatch.setattr(run_wiki, 'ROOT', tmp_path)
    return tmp_path


def test_dry_run_needs_no_models_and_preserves_matched_contexts(tmp_path, monkeypatch):
    root = workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(run_wiki, 'load_models', lambda *a, **kw: pytest.fail('dry run loaded credentials'))
    run_wiki.main(['--run-id', 'preview', '--dry-run'])
    data = json.loads((root / 'runs/preview/preview.json').read_text())['conditions']
    assert data['slow']['context'] == data['working']['context']
    assert data['slow']['context_sha256'] == data['working']['context_sha256']
    with pytest.raises(FileExistsError):
        run_wiki.main(['--run-id', 'preview', '--dry-run'])


def test_live_launcher_records_both_stub_conditions_and_shared_groups(tmp_path, monkeypatch):
    root = workspace(tmp_path, monkeypatch)
    models = root / 'models.yaml'
    models.write_text('models:\n  - name: stub\n    transport: stub\n    model: stub\n    stub_text: \'wait("99999")\'\n')
    run_wiki.main(['--run-id', 'trial', '--models', str(models), '--agent', 'stub',
                   '--env-model', 'stub', '--agents', '2'])
    preview = json.loads((root / 'runs/trial/preview.json').read_text())['conditions']
    for condition in ('slow', 'working'):
        folder = root / 'runs/trial' / condition
        records = [json.loads(p.read_text()) for p in folder.glob('*.json') if p.name != 'manifest.json']
        assert len(records) == 2
        assert {r['group']['agent_id'] for r in records} == {'agent0', 'agent1'}
        assert all(r['context_sha256'] == preview[condition]['context_sha256'] for r in records)
        assert all(r['model_config']['transport'] == 'stub' for r in records)
