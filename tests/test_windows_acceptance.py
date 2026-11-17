"""Isolated test doubles; these are NOT authentic local-model inference evidence."""
import hashlib
import io
import json
import os
import shutil

import pytest
from app import core
from app.acceptance import run_acceptance
from tests.test_core import FAKE_MODULE, fake_engine
from tests.test_local_ai_structured import model_server


def test_pinned_single_module_first_install_and_offline_reopen(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'APPROVED_B143_SOURCE_SHA256', hashlib.sha256(FAKE_MODULE.encode()).hexdigest())
    monkeypatch.setattr(core, 'PINNED_SMOKE_GEOMETRY_SHA256', 'geo')
    targets=[]
    class Response(io.BytesIO):
        pass
    def offline_source_request(req, timeout):
        targets.append(req.full_url)
        return Response(FAKE_MODULE.encode())
    monkeypatch.setattr(core.urllib.request, 'urlopen', offline_source_request)
    manager = core.EngineManager(tmp_path / 'local-user-data')
    output = manager.install(core.BASELINE_SHA)
    assert output['voxels'] == 192
    assert targets == [
        f'https://raw.githubusercontent.com/{core.REPO}/{core.BASELINE_SHA}/{core.ENGINE_FILE}'
    ]
    assert (manager.active_path / core.ENGINE_FILE).is_file()
    assert not (manager.active_path / 'README.md').exists()
    evidence=core.json_read(manager.active_path / '.studio-evidence.json')
    assert evidence['installMethod'] == 'PINNED_SINGLE_MODULE'
    assert evidence['moduleDownloadSha256'] == core.APPROVED_B143_SOURCE_SHA256
    monkeypatch.setattr(core.urllib.request,'urlopen', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('offline')))
    assert core.EngineManager(tmp_path / 'local-user-data').active_path is not None


def test_unapproved_remote_ref_refused_without_request(tmp_path, monkeypatch):
    monkeypatch.setattr(core.urllib.request,'urlopen', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('network request must not happen')))
    manager=core.EngineManager(tmp_path / 'app')
    with pytest.raises(core.StudioError,match='互換性・安全審査対象外'):
        manager.install('c'*40)
    assert manager.active_sha is None
    assert not list(manager.versions.glob('.staging-*'))


def test_pinned_module_tamper_refused_without_activation(tmp_path, monkeypatch):
    monkeypatch.setattr(core.urllib.request, 'urlopen',
                        lambda *a, **kw:io.BytesIO(b'evil changed module'))
    manager=core.EngineManager(tmp_path / 'app')
    with pytest.raises(core.StudioError,match='SHA-256'):
        manager.install(core.BASELINE_SHA)
    assert manager.active_sha is None
    assert not list(manager.versions.glob('.staging-*'))


@pytest.mark.skipif(shutil.which('node') is None or (os.name!='nt' and not os.getenv('DISPLAY')),
                    reason='Node and real GUI display needed')
def test_acceptance_one_real_request_path_with_test_double(tmp_path, monkeypatch):
    # Real Node compile and real Tk rendering, but HTTP server gives a canned
    # test design, NOT true model semantic understanding.
    engine=fake_engine(tmp_path/'engine')
    monkeypatch.setattr(core, 'APPROVED_B143_SOURCE_SHA256', hashlib.sha256(FAKE_MODULE.encode()).hexdigest())
    monkeypatch.setattr(core, 'PINNED_SMOKE_GEOMETRY_SHA256', 'geo')
    with model_server('valid') as (url,requests):
        monkeypatch.setattr(core,'OLLAMA_BASE',url)
        out=tmp_path/'report.json'
        prompt='東京タワーのように細長い図書館を作って（送信内容を報告書に保存しない）'
        code=run_acceptance(['--acceptance','--provider','Ollama','--model','test-model',
                             '--prompt',prompt,'--engine',str(engine),'--report',str(out)])
        report=json.loads(out.read_text())
        assert code==0 and report['outcome']=='PASS_SOURCE_AND_GUI_ONLY'
        assert report['source']['voxelCount']==192
        assert report['sourceValid'] and report['guiRendered']
        assert report['guiCanvasItems']>=4
        assert report['providerIdentityIndependentlyVerified'] is False
        assert report['humanVisualQualityVerified'] is False
        assert report['worldContacts']==report['worldWrites']==0
        assert len(requests)==1
        assert prompt==requests[0]['messages'][1]['content']
        assert prompt not in out.read_text()
        assert 'origin' not in out.read_text()  # do not save the model design


@pytest.mark.skipif(shutil.which('node') is None,reason='Node needed')
def test_acceptance_preflight_fail_does_not_send_prompt(tmp_path, monkeypatch):
    engine=fake_engine(tmp_path/'engine')
    source=engine/core.ENGINE_FILE
    source.write_text(source.read_text(encoding='utf-8')+'\n// unreviewed tampering',encoding='utf-8')
    with model_server('valid') as (url,requests):
        monkeypatch.setattr(core,'OLLAMA_BASE',url)
        out=tmp_path/'fail-report.json'
        assert run_acceptance(['--acceptance','--provider','Ollama','--model','test-model',
                    '--engine',str(engine),'--report',str(out)])==1
        report=json.loads(out.read_text())
        assert report['stage']=='PINNED_ENGINE'
        assert report['actualProviderRequestAttempted'] is False
        assert report['worldWrites']==0
        assert not requests


def test_invalid_model_is_not_started(tmp_path):
    path=tmp_path/'invalid.json'
    assert run_acceptance(['--acceptance','--provider','Ollama','--model','   ',
                           '--report',str(path)])==1
    data=json.loads(path.read_text())
    assert data['actualProviderRequestAttempted'] is False
    assert data['worldContacts']==0 and data['worldWrites']==0


@pytest.mark.skipif(shutil.which('node') is None,reason='Node needed')
def test_acceptance_http_error_does_not_retry(tmp_path,monkeypatch):
    engine=fake_engine(tmp_path/'engine')
    with model_server('server-error') as (url,requests):
        monkeypatch.setattr(core,'OLLAMA_BASE',url)
        report_path=tmp_path/'transport-fail.json'
        assert run_acceptance(['--acceptance','--provider','Ollama','--model','model-which-fails',
                '--engine',str(engine),'--report',str(report_path)])==1
        report=json.loads(report_path.read_text())
        assert report['outcome']=='FAIL'
        assert report['stage']=='LOCAL_MODEL_REQUEST'
        assert report['actualProviderRequestAttempted']
        assert not report['sourceValid'] and not report['guiRendered']
        assert len(requests)==1
        assert report['worldWrites']==0


def test_fixture_cannot_masquerade_as_actual_model(tmp_path):
    """The official CI fixture must never produce genuine-inference evidence."""
    report = tmp_path / 'unmarked-fixture.json'
    assert run_acceptance(['--acceptance', '--provider', 'Ollama',
                           '--model', 'CI_TEST_DOUBLE', '--report', str(report)]) == 1
    data = json.loads(report.read_text(encoding='utf-8'))
    assert data['stage'] == 'PREFLIGHT'
    assert not data['actualProviderRequestAttempted']
    assert not data['testDoubleRequestAttempted']
    assert data['worldWrites'] == 0


@pytest.mark.real_engine
@pytest.mark.skipif(shutil.which('node') is None or (os.name != 'nt' and not os.getenv('DISPLAY')),
                    reason='Real Node and Tk display needed')
def test_explicit_ephemeral_fixture_real_engine_and_tk(tmp_path):
    """Exercises real pinned B143 + Tk, but is explicitly fake-model evidence."""
    import subprocess
    import sys
    import time
    root = os.environ.get('MAB_B143_VERIFIED_ENGINE')
    if not root:
        pytest.skip('Real B143 engine not provided')
    ready = tmp_path / 'fixture-ready.json'
    process = subprocess.Popen([sys.executable, '-m', 'tests.windows_acceptance_fixture',
                                '--port', '0', '--ready-file', str(ready)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(50):
            if ready.exists():
                break
            if process.poll() is not None:
                raise AssertionError('Fixture exited early')
            time.sleep(.05)
        assert ready.exists()
        port = json.loads(ready.read_text(encoding='utf-8'))['port']
        assert 1 <= port <= 65535
        report = tmp_path / 'explicit-fixture.json'
        code = run_acceptance(['--acceptance', '--provider', 'Ollama',
                               '--model', 'CI_TEST_DOUBLE', '--test-double',
                               '--fixture-port', str(port), '--engine', root,
                               '--report', str(report)])
        data = json.loads(report.read_text(encoding='utf-8'))
        assert code == 0
        assert data['outcome'] == 'PASS_TEST_DOUBLE_SOURCE_AND_GUI_ONLY'
        assert data['testDoubleUsed'] and data['testDoubleRequestAttempted']
        assert not data['actualProviderRequestAttempted']
        assert data['sourceValid'] and data['guiRendered']
        assert data['source']['voxelCount'] == 917
        assert data['worldContacts'] == data['worldWrites'] == 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
