"""Regression for 2026-11-17 genuine Gemma limit=6209/ctx=8192.

These are schema/transport and source-cli tests, not an actual model success.
"""
import json
from pathlib import Path
import subprocess
import sys
from app import core
from tests.test_model_streaming import fake_lm_stream
from tests.test_local_llm_flow import DESIGN


def test_first_design_compact_transport_does_not_change_real_b143_contract(monkeypatch):
    assert core.B143_JSON_SCHEMA['properties']['elements']['maxItems'] == 160
    assert core.MAX_MODEL_ELEMENTS == 18
    with fake_lm_stream('normal') as (url, posted):
        monkeypatch.setattr(core, 'LM_STUDIO_BASE', url)
        assert core.ask_llm('LM Studio', 'gemma-4-26b-a4b-it-qat',
                            '未来的な4階建てのビルを作って、窓をたくさん') == DESIGN
        assert len(posted) == 1
        schema = posted[0]['response_format']['json_schema']['schema']
        assert schema['properties']['elements']['maxItems'] == 18
        assert schema['properties']['title']['maxLength'] == 48
        assert schema['properties']['brief']['maxLength'] == 120
        prompt = posted[0]['messages'][0]['content']
        assert '4階建てなら4階を保つ' in prompt
        assert '窓を1個ずつ' in prompt


def test_revision_has_no_compact_first_design_schema(monkeypatch):
    before = {**DESIGN, 'title':'A'*80, 'brief':'B'*300,
              'elements':[dict(DESIGN['elements'][0], id='el'+str(i)) for i in range(22)]}
    with fake_lm_stream('normal') as (url, posted):
        monkeypatch.setattr(core, 'LM_STUDIO_BASE', url)
        core.ask_llm('LM Studio', 'gemma-4-26b-a4b-it-qat',
                     '入口を変えて', revision_base=before)
        schema = posted[0]['response_format']['json_schema']['schema']
        assert schema['properties']['elements']['maxItems'] == 30
        assert schema['properties']['title']['maxLength'] == 100
        assert schema['properties']['brief']['maxLength'] == 500
        assert '初回設計' not in posted[0]['messages'][0]['content']


def test_source_cli_preflight_safely_fails_without_request(tmp_path):
    target = tmp_path/'receipt.json'
    p = subprocess.run([sys.executable, '-m', 'app.acceptance_cli', '--acceptance',
                        '--provider', 'Ollama', '--model', 'CI_TEST_DOUBLE',
                        '--report', str(target), '--console-progress'],
                       cwd=Path(__file__).resolve().parents[1], capture_output=True,
                       text=True, timeout=20)
    assert p.returncode == 1
    data = json.loads(target.read_text(encoding='utf-8'))
    assert data['actualProviderRequestAttempted'] is False
    assert data['worldContacts'] == data['worldWrites'] == 0


def test_source_windows_scripts_crlf_ascii_and_no_installs():
    root = Path(__file__).resolve().parents[1]
    for name in ('RUN_SOURCE_REAL_MODEL_CHECK.bat',
                 'RUN_SOURCE_REAL_MODEL_CHECK.ps1'):
        raw = (root / name).read_bytes()
        assert b'\r\n' in raw and raw.replace(b'\r\n',b'\n').count(b'\r') == 0
        assert raw.isascii()
        assert b'pip install' not in raw.lower()
    ps = (root/'RUN_SOURCE_REAL_MODEL_CHECK.ps1').read_text(encoding='ascii')
    assert 'CI_TEST_DOUBLE' not in ps
    assert 'RUN_ONE_SOURCE_ACCEPTANCE' in ps
    assert 'http://127.0.0.1:1234/api/v1/models' in ps
    assert '--console-progress' in ps
    assert 'LM_STUDIO_SELECTED_MODEL_NOT_LOADED' in ps
    assert 'Using the actually loaded instance' not in ps
