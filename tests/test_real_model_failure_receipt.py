"""A genuine-path failure still records its elapsed time, without retry."""
import json
from app import core
from app.acceptance import run_acceptance
from tests.test_core import fake_engine
from tests.test_local_ai_structured import model_server


def test_real_model_request_failure_receipt_keeps_request_duration(tmp_path, monkeypatch):
    engine = fake_engine(tmp_path / 'engine')
    with model_server('server-error') as (url, requests):
        monkeypatch.setattr(core, 'OLLAMA_BASE', url)
        report_path = tmp_path / 'receipt.json'
        rc = run_acceptance(['--acceptance', '--provider', 'Ollama',
                             '--model', 'test-model', '--engine', str(engine),
                             '--report', str(report_path)])
        receipt = json.loads(report_path.read_text(encoding='utf-8'))
        assert rc == 1 and receipt['stage'] == 'LOCAL_MODEL_REQUEST'
        assert receipt['failureClass'] in ('ModelTransportError', 'StudioError')
        assert receipt['actualProviderRequestAttempted'] is True
        assert receipt['providerRequestSeconds'] >= 0
        assert receipt['worldWrites'] == 0
        assert len(requests) == 1, 'Never retry an expensive failed inference'
