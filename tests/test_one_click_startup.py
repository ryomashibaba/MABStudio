"""Guard zero-manual-work startup without installing packages in unit tests."""
import copy
import json
from pathlib import Path

from app import one_click as oc


def test_plan_does_not_install_run_model_or_touch_world(capsys, monkeypatch):
    monkeypatch.setattr(oc, 'launch', lambda: (_ for _ in ()).throw(AssertionError('launch called')))
    assert oc.main(['--report-only']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['mode'] == 'NO_INSTALL_NO_LAUNCH'
    assert report['worldWrites'] == 0
    assert 'vtk==9.6.2' in report['dependencies']


def test_qa_receipt_is_strictly_bound_to_fingerprint_and_read_only():
    receipt = {'result': 'PASS_NATIVE_READONLY_SELF_CHECK',
               'runtimeFingerprint': 'sha', 'source': 'SYNTHETIC_IN_MEMORY',
               'worldWrites': 0, 'worldContacts': 0}
    assert oc.valid_check_receipt(receipt, 'sha')
    for key, bad in [('result', 'FAIL'), ('runtimeFingerprint', 'different'),
                     ('source', 'REAL_USER_PROJECT'), ('worldWrites', 1),
                     ('worldContacts', 1)]:
        changed = copy.copy(receipt)
        changed[key] = bad
        assert not oc.valid_check_receipt(changed, 'sha')


def test_private_windows_runtime_paths_are_isolated(tmp_path):
    assert oc.private_python(tmp_path) == tmp_path / 'Scripts/python.exe'
    assert oc.gui_python(tmp_path) == tmp_path / 'Scripts/pythonw.exe'
    assert oc.private_python(tmp_path, windows=False) == tmp_path / 'bin/python'


def test_report_failure_never_claims_native_and_world_writes():
    r = oc._startup_result(False, reason='TEST_INSTALL_FAILURE')
    assert r['display'] == 'LIGHTWEIGHT_FALLBACK'
    assert r['windowsUserVisualQA'] == 'NOT_TESTED'
    assert r['realModelCalls'] == r['minecraftWorldReads'] == r['minecraftWorldWrites'] == 0


def test_cached_native_qa_skips_duplicate_render_and_installs(tmp_path, monkeypatch):
    monkeypatch.setattr(oc, 'ROOT', tmp_path)
    monkeypatch.setattr(oc, 'RUNTIME', tmp_path / '.venv-native')
    monkeypatch.setattr(oc, 'SELF_CHECK', tmp_path / 'last-check.json')
    monkeypatch.setattr(oc, '_check_python', lambda: None)
    py = oc.private_python(tmp_path / '.venv-native')
    py.parent.mkdir(parents=True)
    py.write_bytes(b'fake-for-unit-test')
    (tmp_path / 'app').mkdir()
    (tmp_path / 'app/native_viewer.py').write_text('class Test: pass')
    (tmp_path / 'app/native_selfcheck.py').write_text('class Test: pass')
    finger = oc.runtime_fingerprint(py)
    oc._write_json(oc.SELF_CHECK, {'result': 'PASS_NATIVE_READONLY_SELF_CHECK',
                                   'runtimeFingerprint': finger,
                                   'source': 'SYNTHETIC_IN_MEMORY',
                                   'worldWrites': 0, 'worldContacts': 0})
    calls = []
    monkeypatch.setattr(oc, '_command', lambda args, **kw: calls.append(args) or 0)
    result_py, report = oc._prepare_runtime()
    assert result_py == py
    assert report['result'] == 'PASS_NATIVE_READONLY_SELF_CHECK'
    assert len(calls) == 1  # import probe only, NO pip, no render or AI calls


def test_first_qa_run_and_receipt_tied_to_code(tmp_path, monkeypatch):
    monkeypatch.setattr(oc, 'ROOT', tmp_path)
    monkeypatch.setattr(oc, 'RUNTIME', tmp_path / '.venv-native')
    monkeypatch.setattr(oc, 'SELF_CHECK', tmp_path / 'last-check.json')
    monkeypatch.setattr(oc, 'LOCAL', tmp_path / 'status')
    monkeypatch.setattr(oc, 'LOG', tmp_path / 'status/log')
    monkeypatch.setattr(oc, '_check_python', lambda: None)
    py = oc.private_python(tmp_path / '.venv-native')
    py.parent.mkdir(parents=True)
    py.write_bytes(b'fake-for-unit-test')
    (tmp_path / 'app').mkdir()
    (tmp_path / 'app/native_viewer.py').write_text('a=1')
    (tmp_path / 'app/native_selfcheck.py').write_text('b=2')
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        if 'app.native_selfcheck' in cmd:
            oc._write_json(oc.SELF_CHECK, {'result': 'PASS_NATIVE_READONLY_SELF_CHECK',
                                           'source': 'SYNTHETIC_IN_MEMORY',
                                           'worldWrites': 0, 'worldContacts': 0})
        return 0
    monkeypatch.setattr(oc, '_command', fake_run)
    _, checked = oc._prepare_runtime()
    assert checked['runtimeFingerprint'] == oc.runtime_fingerprint(py)
    assert len(calls) == 2
    assert calls[1][2] == 'app.native_selfcheck'


def test_failed_native_setup_auto_falls_back_without_user_decision(tmp_path, monkeypatch):
    monkeypatch.setattr(oc, 'LOCAL', tmp_path)
    monkeypatch.setattr(oc, 'LOG', tmp_path/'start.log')
    monkeypatch.setattr(oc, 'REPORT', tmp_path/'report.json')
    monkeypatch.setattr(oc, '_prepare_runtime', lambda: (_ for _ in ()).throw(RuntimeError('FAKE_SETUP_FAIL')))
    alerts, spawned = [], []
    monkeypatch.setattr(oc, '_alert', alerts.append)
    class Process:
        def wait(self, timeout):
            return 0
    monkeypatch.setattr(oc, '_spawn_app', lambda py, *, native: spawned.append(native) or Process())
    assert oc.launch() == 0
    assert spawned == [False] and not alerts  # no modal dialog for automatic fallback
    report = json.loads(oc.REPORT.read_text(encoding='utf-8'))
    assert report['display'] == 'LIGHTWEIGHT_FALLBACK'
    assert report['minecraftWorldWrites'] == 0


def test_native_app_early_failure_recovers_to_lightweight(tmp_path, monkeypatch):
    monkeypatch.setattr(oc, 'LOCAL', tmp_path)
    monkeypatch.setattr(oc, 'LOG', tmp_path/'start.log')
    monkeypatch.setattr(oc, 'REPORT', tmp_path/'report.json')
    monkeypatch.setattr(oc, '_prepare_runtime', lambda: (Path('test.exe'), {'result':'PASS_NATIVE_READONLY_SELF_CHECK'}))
    seen = []
    class Process:
        def __init__(self, native):
            self.native = native
        def wait(self, timeout):
            return 2 if self.native else 0
    monkeypatch.setattr(oc, '_spawn_app', lambda py, *, native: seen.append(native) or Process(native))
    assert oc.launch() == 0
    assert seen == [True, False]
    assert json.loads(oc.REPORT.read_text(encoding='utf-8'))['display'] == 'LIGHTWEIGHT_FALLBACK'


def test_windows_launcher_and_alias_avoid_browser_or_global_pip():
    root = Path(__file__).resolve().parents[1]
    entry = (root/'START_MAB_STUDIO.bat').read_text(encoding='utf-8')
    alias = (root/'RUN_STUDIO_NATIVE.bat').read_text(encoding='utf-8')
    assert 'app.one_click' in entry
    assert 'START_MAB_STUDIO.bat' in alias
    assert not any(x in entry.lower() for x in ('start http', 'webview', 'chrome', 'pip install', 'powershell'))
