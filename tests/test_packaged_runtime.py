"""Distribution must work without unrelated Python/Node installations.

Static checks are not Windows EXE evidence; Windows Actions must separately
build and launch the actual onedir executable.
"""
import sys
from pathlib import Path
import shutil
import pytest
from app import core, release_runtime


def test_source_checkout_does_not_claim_portable_bundle(monkeypatch):
    monkeypatch.delattr(sys, 'frozen', raising=False)
    assert release_runtime.verify_packaged_runtime() == {
        'mode': 'SOURCE', 'packagedRuntimeVerified': False}


def _frozen(monkeypatch, tmp_path):
    dist = tmp_path / 'dist' / 'MABStudio'
    (dist / '_internal' / 'app' / 'education_runtime').mkdir(parents=True)
    exe = dist / 'MABStudio.exe'
    exe.write_bytes(b'dummy EXE')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(exe))
    monkeypatch.setattr(sys, '_MEIPASS', str(dist / '_internal'), raising=False)
    return dist


def test_broken_frozen_app_never_uses_developer_path_node(tmp_path, monkeypatch):
    dist = _frozen(monkeypatch, tmp_path)
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/node')
    with pytest.raises(core.StudioError, match='node.exe'):
        core.node_binary()
    (dist / 'NODE_LICENSE.txt').write_text('test license')
    with pytest.raises(core.StudioError, match='node.exe'):
        release_runtime.verify_packaged_runtime()


def test_frozen_app_requires_license_bridge_and_transport(tmp_path, monkeypatch):
    dist = _frozen(monkeypatch, tmp_path)
    (dist / 'node.exe').write_bytes(b'fake node')
    with pytest.raises(core.StudioError, match='ライセンス'):
        release_runtime.verify_packaged_runtime()
    (dist / 'NODE_LICENSE.txt').write_text('test license')
    with pytest.raises(core.StudioError, match='MAB接続'):
        release_runtime.verify_packaged_runtime()
    internal = dist / '_internal' / 'app'
    (internal / 'engine_bridge.mjs').write_text('// dummy')
    with pytest.raises(core.StudioError, match='読み取り専用部品'):
        release_runtime.verify_packaged_runtime()
    (internal / 'education_runtime' / 'bridge.mjs').write_text('// dummy')
    (internal / 'education_runtime' / 'node_modules' / 'socket-be').mkdir(parents=True)
    (internal / 'education_runtime' / 'node_modules' / 'socket-be' / 'package.json').write_text('{}')
    report = release_runtime.verify_packaged_runtime()
    assert report['packagedRuntimeVerified'] is True
    assert report['worldWrites'] == report['worldContacts'] == 0
    assert core.node_binary() == str(dist / 'node.exe')


def test_windows_launcher_prefers_packaged_exe_and_never_repackages_it():
    launcher = Path(__file__).resolve().parents[1] / 'start_windows.bat'
    source = launcher.read_text(encoding='utf-8').lower()
    assert source.index('if exist "dist\\mabstudio\\mabstudio.exe"') < source.index('where py')
    assert 'start "" "dist\\mabstudio\\mabstudio.exe"' in source
    assert 'if not exist "dist\\mabstudio\\node.exe" goto :broken' in source
    assert 'python -m app.main' in source  # source-only fallback


def test_incomplete_package_fails_before_any_model_inference(tmp_path, monkeypatch):
    import json
    from app.acceptance import run_acceptance
    _frozen(monkeypatch, tmp_path)
    report = tmp_path / 'acceptance.json'
    result = run_acceptance([
        '--acceptance', '--provider', 'Ollama', '--model', 'pretend-loaded',
        '--report', str(report),
    ])
    record = json.loads(report.read_text(encoding='utf-8'))
    assert result == 1 and record['stage'] == 'PACKAGED_RUNTIME'
    assert not record['actualProviderRequestAttempted']
    assert record['worldContacts'] == record['worldWrites'] == 0


def test_broken_packaged_gui_displays_error_instead_of_console_traceback(monkeypatch):
    from app import main
    from app import release_runtime as runtime
    shown = []
    monkeypatch.setattr(runtime, 'verify_packaged_runtime',
                        lambda: (_ for _ in ()).throw(core.StudioError('配布用部品が不足')))
    monkeypatch.setattr(main.messagebox, 'showerror',
                        lambda title, message: shown.append((title, message)))
    assert main.main() == 1
    assert shown and '配布用部品が不足' in shown[0][1]
