"""Local AI readiness is diagnostic, never a false real-inference claim."""
from app import diagnostics
from app import core


class Manager:
    active_path = None


def test_local_setup_reports_known_absence_without_claiming_inference(monkeypatch):
    monkeypatch.setattr(diagnostics, 'node_binary', lambda: 'node')
    monkeypatch.setattr(diagnostics, 'llm_models', lambda name: ['verified-model-id'])
    outcome = diagnostics.check_local_setup(Manager(), 'Ollama')
    assert outcome['modelServer'] == 'CONNECTED'
    assert outcome['engine'] == 'NOT_INSTALLED'
    assert outcome['designReadyToTry'] is False
    assert outcome['inference'] == 'NOT_TESTED'
    assert outcome['educationWebSocket'] == 'NOT_TESTED'
    assert outcome['worldContacts'] == 0 and outcome['worldWrites'] == 0
    assert '推論の成功を意味しません' in diagnostics.readiness_text(outcome)


def test_local_setup_blocks_invalid_provider():
    import pytest
    with pytest.raises(core.StudioError):
        diagnostics.check_local_setup(Manager(), 'https://not-local.example')


def test_diagnosis_does_not_infer_success_when_offline(monkeypatch):
    def fail(*args): raise OSError('connection refused')
    monkeypatch.setattr(diagnostics, 'node_binary', fail)
    monkeypatch.setattr(diagnostics, 'llm_models', fail)
    outcome = diagnostics.check_local_setup(Manager(), 'LM Studio')
    assert outcome['node'] == 'NOT_FOUND'
    assert outcome['modelServer'] == 'NOT_CONNECTED'
    assert outcome['designReadyToTry'] is False
