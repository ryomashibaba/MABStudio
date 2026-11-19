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
