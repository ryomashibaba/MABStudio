"""One focused offline safety test for Drive-synced release and V11 handoff."""
from pathlib import Path

from app.examples import example_library
from app.v11_watch import newest_v11_ready_source

ROOT = Path(__file__).resolve().parents[1]


def test_v11_handoff_is_detected_only_when_published_and_source_compatible(tmp_path):
    import json
    inbox = tmp_path / 'v11-inbox'
    inbox.mkdir()
    assert newest_v11_ready_source(tmp_path) is None
    source = example_library()
    path = inbox / 'V11_READY_SOURCE.json'
    path.write_text(json.dumps(source, ensure_ascii=False), encoding='utf-8')
    detected = newest_v11_ready_source(tmp_path)
    assert detected and detected[0] == path and detected[2] == source['title']
    assert newest_v11_ready_source(tmp_path, detected[1]) is None
    # New V11 shapes must not be converted by Studio.
    source['elements'][0]['shape'] = 'staircase'
    path.write_text(json.dumps(source, ensure_ascii=False), encoding='utf-8')
    assert newest_v11_ready_source(tmp_path) is None


def test_optional_sync_folder_does_not_change_approved_history(tmp_path):
    import json
    root = tmp_path / 'studio'
    root.mkdir()
    feed = tmp_path / 'drive'
    feed.mkdir()
    config = root / 'auto-update'
    config.mkdir()
    (config / 'trusted-feed.txt').write_text(str(feed), encoding='utf-8')
    path = feed / 'V11_READY_SOURCE.json'
    source = example_library()
    source['title'] = 'V11 sync ready'
    path.write_text(json.dumps(source), encoding='utf-8')
    got = newest_v11_ready_source(root)
    assert got and got[0] == path and got[2] == 'V11 sync ready'
    assert not (root / 'projects').exists()


def test_existing_launcher_checks_release_without_model_or_world_operations():
    launch = (ROOT / 'OPEN_MAB_STUDIO.bat').read_text(encoding='ascii')
    updater = (ROOT / 'AUTO_UPDATE_STUDIO.ps1').read_text(encoding='utf-8-sig')
    assert 'AUTO_UPDATE_STUDIO.ps1' in launch
    assert 'STUDIO_RELEASE.json' in updater
    assert 'Get-StudioFileSha256 -Path $archive' in updater
    assert 'RUN_WINDOWS_NATIVE_QA.bat' in updater
    assert 'WINDOWS_QA_RECEIPT_MISSING_FOR_NEW_RELEASE' in updater
    # The QA batch file's verbose output must not pollute the path returned
    # by Try-StageUpdate (PowerShell functions return all success output).
    assert "& (Join-Path $destination 'RUN_WINDOWS_NATIVE_QA.bat') | Out-Host" in updater
    assert "& (Join-Path $launchFrom 'OPEN_MAB_STUDIO.ps1')" in updater
    assert 'START_MAB_STUDIO.bat' not in updater
    assert 'Invoke-RestMethod' not in updater and 'WorldWrite' not in updater


def test_public_v11_ready_source_is_staged_as_unapproved_json_only(tmp_path):
    import base64
    import hashlib
    import io
    import json
    from app.v11_watch import (sync_v11_github_ready_source, REMOTE_READY_NAME,
                               newest_v11_ready_source)

    source = example_library()
    raw = json.dumps(source, ensure_ascii=False).encode('utf-8')
    blob_sha = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\x00' + raw).hexdigest()
    contents = json.dumps({'type': 'file', 'size': len(raw), 'sha': blob_sha,
                           'encoding': 'base64',
                           'content': base64.b64encode(raw).decode('ascii')}).encode()

    class Response(io.BytesIO):
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()

    def fake_open(request, timeout):
        assert request.full_url.startswith('https://api.github.com/repos/ryomashibaba/MinecraftAIBuilder/')
        assert timeout <= 10
        return Response(contents)

    assert sync_v11_github_ready_source(tmp_path, opener=fake_open)
    path = tmp_path / 'v11-inbox' / REMOTE_READY_NAME
    assert path.read_bytes() == raw
    assert newest_v11_ready_source(tmp_path)[0] == path
    assert not (tmp_path / 'projects').exists()  # no approval or Minecraft writes
    assert not sync_v11_github_ready_source(tmp_path, opener=fake_open)


def test_public_v11_rejects_unsupported_or_tampered_artifact_without_overwrite(tmp_path):
    import base64
    import hashlib
    import io
    import json
    import pytest
    from app.v11_watch import sync_v11_github_ready_source, REMOTE_READY_NAME
    from app.core import StudioError

    inbox = tmp_path / 'v11-inbox'
    inbox.mkdir()
    original = b'old-safe-design'
    target = inbox / REMOTE_READY_NAME
    target.write_bytes(original)
    source = example_library()
    source['elements'][0]['shape'] = 'staircase'
    raw = json.dumps(source).encode()

    def run(sha):
        data = json.dumps({'type': 'file', 'encoding': 'base64',
                           'sha': sha, 'size': len(raw),
                           'content': base64.b64encode(raw).decode()}).encode()
        return sync_v11_github_ready_source(
            tmp_path, opener=lambda *args, **kwargs: _dummy_response(data))

    class _dummy_response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *args): self.close()

    good_sha = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\x00'+raw).hexdigest()
    assert run(good_sha) is False
    assert target.read_bytes() == original
    with pytest.raises(StudioError, match='整合性'):
        run('0'*40)
    assert target.read_bytes() == original


def test_portable_release_metadata_and_zip_reject_untrusted_packages(tmp_path):
    import io
    import hashlib
    import json
    import zipfile
    import pytest
    from app.portable_update import _download_metadata, _validate_zip, REPO, ASSET_NAME

    sha = 'a' * 40
    tag = 'mabstudio-v03-' + sha
    url = 'https://github.com/' + REPO + '/releases/download/' + tag + '/' + ASSET_NAME
    meta = {
        'tag_name': tag, 'draft': False, 'prerelease': False,
        'target_commitish': sha, 'published_at': '2026-11-21T12:00:00Z',
        'assets': [{'name': ASSET_NAME, 'size': 150_000_000,
                    'digest': 'sha256:' + 'b' * 64,
                    'browser_download_url': url}],
    }
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *_): self.close()
    def opener(req, timeout):
        assert req.full_url.endswith('/releases/latest') and timeout <= 5
        return Response(json.dumps(meta).encode())
    assert _download_metadata(opener=opener)['sha'] == sha
    meta['target_commitish'] = 'main'
    with pytest.raises(ValueError, match='RELEASE_SHA'):
        _download_metadata(opener=opener)
    meta['target_commitish'] = sha
    meta['assets'][0]['digest'] = None
    with pytest.raises(ValueError, match='ASSET_NOT_VERIFIABLE'):
        _download_metadata(opener=opener)
    broken = tmp_path / 'bad.zip'
    with zipfile.ZipFile(broken, 'w') as z:
        for i in range(12):
            z.writestr('MABStudio/test' + str(i), b'0')
        z.writestr('../escape.txt', b'x')
    with pytest.raises(ValueError, match='UNSAFE_PATH'):
        _validate_zip(broken)


def test_portable_updater_does_not_run_during_source_or_without_consent_env(monkeypatch):
    from app.portable_update import prepare_update, launch_ready_at_startup
    import sys
    monkeypatch.delattr(sys, 'frozen', raising=False)
    assert not prepare_update()
    assert not launch_ready_at_startup()
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setenv('MAB_STUDIO_DISABLE_AUTO_UPDATE', '1')
    assert not prepare_update()
    assert not launch_ready_at_startup()


def test_portable_update_stages_and_switches_without_touching_world(tmp_path, monkeypatch):
    import hashlib
    import io
    import json
    import shutil
    import subprocess
    import sys
    import zipfile
    from types import SimpleNamespace
    from app import portable_update as updater

    sha_old, sha_new = 'a'*40, 'b'*40
    base = tmp_path / 'user-data' / 'auto-update'
    current = tmp_path / 'current' / 'MABStudio.exe'
    current.parent.mkdir()
    current.write_bytes(b'old-test-exe')
    (current.parent/'RUNTIME_MANIFEST.json').write_text(json.dumps({
        'schema':'mab-studio-critical-runtime-manifest-v1',
        'studioGitSha': sha_old, 'builtAtUtc':'2026-11-20T11:00:00Z',
    }), encoding='utf-8')
    filedata = io.BytesIO()
    with zipfile.ZipFile(filedata, 'w') as z:
        files = {
            'MABStudio/MABStudio.exe': b'new-test-exe',
            'MABStudio/node.exe': b'fake-node',
            'MABStudio/RUNTIME_MANIFEST.json': json.dumps({
                'schema':'mab-studio-critical-runtime-manifest-v1',
                'studioGitSha':sha_new,
                'builtAtUtc':'2026-11-21T11:00:00Z',
            }).encode(),
            'MABStudio/_internal/app/education_runtime/build.mjs': b'// fake test file',
        }
        files.update({'MABStudio/_internal/test' + str(i) + '.txt': b'nonempty'
                      for i in range(8)})
        for path, content in files.items():
            z.writestr(path, content)
    archive_data = filedata.getvalue()
    release = {'sha':sha_new, 'digest': hashlib.sha256(archive_data).hexdigest(),
               'size':len(archive_data), 'publishedAt':'2026-11-21T12:00:00Z',
               'url':'https://github.com/ryomashibaba/MABStudio/releases/download/test/test.zip'}
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'executable',str(current))
    monkeypatch.setattr(updater,'_base',lambda:base)
    monkeypatch.setattr(updater,'_download_metadata',lambda:release)

    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self,*args): self.close()
    assert updater.prepare_update.__module__ == 'app.portable_update'
    monkeypatch.setattr(updater.urllib.request,'urlopen',lambda *args,**kw: Response(archive_data))
    assert updater.prepare_update()
    dest = base/'portable-versions'/sha_new/'MABStudio'/'MABStudio.exe'
    assert dest.read_bytes()==b'new-test-exe'
    assert not (base/'projects').exists()
    assert not updater.prepare_update()  # already staged, no second download

    observed = []
    def fake_native(cmd,**kwargs):
        assert '--native-selfcheck' in cmd
        report=Path(cmd[cmd.index('--report')+1])
        report.write_text(json.dumps({'result':'PASS_NATIVE_READONLY_SELF_CHECK'}))
        observed.append('native')
        return subprocess.CompletedProcess(cmd,0)
    class FakeChild:
        def poll(self): return None
    def fake_gui(cmd,**kwargs):
        assert cmd == [str(dest)]
        observed.append('gui')
        return FakeChild()
    monkeypatch.setattr(updater.subprocess,'run',fake_native)
    monkeypatch.setattr(updater.subprocess,'Popen',fake_gui)
    monkeypatch.setattr(updater.time,'sleep',lambda _:None)
    assert updater.launch_ready_at_startup()
    assert observed==['native','gui']
    assert current.read_bytes()==b'old-test-exe'
    # Failed-version cooldown stops repeated GUI crashes on future launches.
    (base/'portable-failed.json').write_text(json.dumps({
        'sha':sha_new, 'at':'2026-11-21T11:00:00+00:00',
    }))
    # Simulated current clock may be later than 12h; pin it to runtime UTC.
    from datetime import datetime,timezone
    (base/'portable-failed.json').write_text(json.dumps({
        'sha':sha_new, 'at':datetime.now(timezone.utc).isoformat(),
    }))
    assert not updater.launch_ready_at_startup()
