"""Distribution must work without unrelated Python/Node installations.

Static checks are not Windows EXE evidence; Windows Actions must separately
build and launch the actual onedir executable.
"""
import sys
import json
from pathlib import Path
import shutil
import pytest
from app import core, release_runtime, release_audit


def test_source_checkout_does_not_claim_portable_bundle(monkeypatch):
    monkeypatch.delattr(sys, 'frozen', raising=False)
    assert release_runtime.verify_packaged_runtime() == {
        'mode': 'SOURCE', 'packagedRuntimeVerified': False}


def _copy_gpu_assets(dist):
    """Simulated release includes the same read-only bundled GPU assets."""
    source=Path(__file__).resolve().parents[1]/'app'/'gpu_viewer_assets'
    target=dist/'_internal'/'app'/'gpu_viewer_assets'
    target.mkdir(parents=True, exist_ok=True)
    for name in ('viewer.html', 'viewer.js'):
        (target/name).write_bytes((source/name).read_bytes())


def _frozen(monkeypatch, tmp_path):
    dist = tmp_path / 'dist' / 'MABStudio'
    (dist / '_internal' / 'app' / 'education_runtime').mkdir(parents=True)
    _copy_gpu_assets(dist)
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
    from app.education import education_bridge_script
    (internal / 'education_runtime' / 'bridge.mjs').write_bytes((Path(__file__).resolve().parents[1] / 'app/education_runtime/bridge.mjs').read_bytes())
    (internal / 'education_runtime' / 'build.mjs').write_bytes((Path(__file__).resolve().parents[1] / 'app/education_runtime/build.mjs').read_bytes())
    (internal / 'education_runtime' / 'package.json').write_text(json.dumps({'dependencies': {'socket-be':'2.8.0'}}))
    (internal / 'education_runtime' / 'node_modules' / 'socket-be').mkdir(parents=True)
    (internal / 'education_runtime' / 'node_modules' / 'socket-be' / 'package.json').write_text(json.dumps({'version':'2.8.0','license':'MIT'}))
    with pytest.raises(core.StudioError, match='整合性記録'):
        release_runtime.verify_packaged_runtime()
    manifest=release_audit.build_manifest(dist, exercise=False)
    (dist / release_audit.MANIFEST).write_text(json.dumps(manifest))
    report = release_runtime.verify_packaged_runtime()
    assert report['packagedRuntimeVerified'] is True
    assert report['bundleManifestVerified'] is True
    assert report['criticalAssets'] >= 7
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


def test_bundle_modification_and_missing_socketbe_file_rejected(tmp_path, monkeypatch):
    from app.education import education_bridge_script
    dist = _frozen(monkeypatch, tmp_path)
    (dist / 'node.exe').write_bytes(b'private node executable for unit test')
    (dist / 'NODE_LICENSE.txt').write_text('local license placeholder')
    internal = dist / '_internal' / 'app'
    (internal / 'engine_bridge.mjs').write_bytes((Path(__file__).resolve().parents[1] / 'app/engine_bridge.mjs').read_bytes())
    transport = internal / 'education_runtime'
    (transport / 'bridge.mjs').write_bytes((Path(__file__).resolve().parents[1] / 'app/education_runtime/bridge.mjs').read_bytes())
    (transport / 'build.mjs').write_bytes((Path(__file__).resolve().parents[1] / 'app/education_runtime/build.mjs').read_bytes())
    (transport / 'package.json').write_text(json.dumps({'dependencies':{'socket-be':'2.8.0'}}))
    (transport / 'node_modules/socket-be').mkdir(parents=True)
    (transport / 'node_modules/socket-be/package.json').write_text(json.dumps({'version':'2.8.0'}))
    manifest=release_audit.build_manifest(dist, exercise=False)
    (dist / release_audit.MANIFEST).write_text(json.dumps(manifest))
    assert release_runtime.verify_packaged_runtime()['bundleManifestVerified']
    (dist / 'node.exe').write_bytes(b'altered private Node.exe')
    with pytest.raises(core.StudioError, match='変更されています'):
        release_runtime.verify_packaged_runtime()
    (dist / 'node.exe').write_bytes(b'private node executable for unit test')
    (transport / 'node_modules/socket-be/entry.mjs').write_text('// unexpected addition')
    with pytest.raises(core.StudioError, match='構成がビルド時と異なります'):
        release_runtime.verify_packaged_runtime()
    (transport / 'node_modules/socket-be/entry.mjs').unlink()
    (transport / 'node_modules/socket-be/package.json').write_text(json.dumps({'version':'2.9.0'}))
    with pytest.raises(core.StudioError):
        release_runtime.verify_packaged_runtime()


def test_windows_build_runs_private_node_audit_before_exe_launch():
    base = Path(__file__).resolve().parents[1]
    bat = (base / 'build_windows.bat').read_text(encoding='utf-8').lower()
    ci = (base / '.github/workflows/windows-build.yml').read_text(encoding='utf-8').lower()
    assert 'python.exe" -m app.release_audit "dist\\mabstudio"' in bat
    assert ci.index('python -m app.release_audit dist/mabstudio') < ci.index('launch real packaged exe')


def test_release_audit_checks_private_node_permission_and_socket_import(tmp_path, monkeypatch):
    from app.education import education_bridge_script
    import subprocess
    dist=tmp_path/'MABStudio'; (dist / '_internal/app/education_runtime/node_modules/socket-be').mkdir(parents=True)
    (dist / 'MABStudio.exe').write_bytes(b'x')
    (dist / 'node.exe').write_bytes(b'x')
    (dist / 'NODE_LICENSE.txt').write_bytes(b'x')
    (dist / '_internal/app/engine_bridge.mjs').write_bytes(b'x')
    _copy_gpu_assets(dist)
    transport=dist / '_internal/app/education_runtime'
    (transport/'bridge.mjs').write_bytes((Path(__file__).resolve().parents[1] / 'app/education_runtime/bridge.mjs').read_bytes())
    (transport/'build.mjs').write_bytes((Path(__file__).resolve().parents[1] / 'app/education_runtime/build.mjs').read_bytes())
    (transport/'package.json').write_text(json.dumps({'dependencies':{'socket-be':'2.8.0'}}))
    (transport/'node_modules/socket-be/package.json').write_text(json.dumps({'version':'2.8.0'}))
    calls=[]
    def fake_run(cmd, **kw):
        calls.append((cmd,kw))
        return subprocess.CompletedProcess(cmd,0,'v22.16.0\n' if cmd[1:] == ['--version'] else 'PASS\n','')
    monkeypatch.setattr(release_audit.subprocess,'run',fake_run)
    audit=release_audit.build_manifest(dist,exercise=True)
    assert audit['nodeVersionAtBuild']=='v22.16.0'
    assert len(calls)==5
    assert any('--permission' in args for args,_ in calls)
    assert any('--self-test' in args for args,_ in calls)
    assert any('socket-be' in ' '.join(args) for args,_ in calls)
