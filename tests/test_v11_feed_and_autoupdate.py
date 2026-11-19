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
    assert "& (Join-Path $launchFrom 'OPEN_MAB_STUDIO.ps1')" in updater
    assert 'START_MAB_STUDIO.bat' not in updater
    assert 'Invoke-RestMethod' not in updater and 'WorldWrite' not in updater
