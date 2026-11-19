"""Distribution regression for actual .bat/.ps1 bytes on Japanese Windows cmd.exe."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_all_batch_scripts_are_ascii_and_crlf():
    paths = sorted(ROOT.glob('*.bat'))
    assert {'build_windows.bat', 'prepare_education_readonly.bat',
            'RUN_WINDOWS_NATIVE_QA.bat', 'start_windows.bat'} <= {p.name for p in paths}
    for path in paths:
        data = path.read_bytes()
        assert data.startswith(b'@echo off\r\n'), path.name
        assert data.isascii(), f'cmd.exe locale-dependent characters in {path.name}'
        assert b'\n' in data and data.count(b'\n') == data.count(b'\r\n'), path.name
        assert data.endswith(b'\r\n'), path.name
        assert b'\xef\xbb\xbf' not in data, path.name


def test_powershell_51_unicode_scripts_use_utf8_bom():
    paths = list(ROOT.glob('*.ps1'))
    assert paths
    for path in paths:
        raw = path.read_bytes()
        assert raw.count(b'\n') == raw.count(b'\r\n'), path.name
        if any(x >= 128 for x in raw):
            assert raw.startswith(b'\xef\xbb\xbf'), path.name
            raw.decode('utf-8-sig')


def test_packaged_windows_qa_rejects_bad_batches_before_downloads():
    code = (ROOT / 'RUN_WINDOWS_NATIVE_QA.ps1').read_text(encoding='utf-8-sig')
    assert code.index('LF_ONLY_WINDOWS_BATCH_FILE') < code.index('PINNED_B143_DOWNLOAD')
    assert code.index('NON_ASCII_WINDOWS_BATCH_FILE') < code.index('PINNED_B143_DOWNLOAD')
    assert 'ISOLATED_WINDOWS_BUILD_FAILED' in code


def test_education_runtime_is_only_pinned_readonly_dependency():
    import json
    top = json.loads((ROOT / 'app/education_runtime/package.json').read_text(encoding='utf-8'))
    assert top['dependencies'] == {'socket-be': '2.8.0'}
    batch = (ROOT / 'prepare_education_readonly.bat').read_text(encoding='ascii')
    assert 'npm.cmd install --prefix "app\\education_runtime"' in batch
    assert '--ignore-scripts' in batch
    assert '--self-test' in batch


def test_native_build_accepts_registered_python313_without_py_launcher():
    batch = (ROOT / 'build_windows_native.bat').read_text(encoding='ascii')
    assert 'python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,13) else 1)"' in batch
    assert 'python -m venv .venv-native' in batch
    assert 'py -3.13 -m venv .venv-native' in batch
    assert 'npm install -g' not in batch


def test_windows_packaged_native_qa_checks_real_pixels_and_large_synthetic_scene():
    native = (ROOT / 'app/native_selfcheck.py').read_text(encoding='utf-8')
    workflow = (ROOT / '.github/workflows/windows-build.yml').read_text(encoding='utf-8')
    qa = (ROOT / 'RUN_WINDOWS_NATIVE_QA.ps1').read_text(encoding='utf-8-sig')
    assert '_image_signature' in native
    assert 'NATIVE_3D_FRAMEBUFFER_UNCHANGED_AFTER_PALETTE' in native
    for script in (workflow, qa):
        assert "'--extended'" in script
        assert 'extendedScene.cells -ne 3128' in script
        assert 'paletteChangePixelsVerified' in script
        assert 'WaitForExit(120000)' in script
        assert 'Stop-Process' in script


def test_native_qa_reuses_only_exact_sha_verified_local_b143_when_available():
    code = (ROOT / 'RUN_WINDOWS_NATIVE_QA.ps1').read_text(encoding='utf-8-sig')
    assert "Join-Path $studioDataRoot 'engines'" in code
    assert "(Get-NativeSha256 -Path $localEngine) -cne $summary.sourcePinnedHash" in code
    assert "LOCAL_PINNED_B143_HASH_MISMATCH" in code
    assert "Copy-Item -LiteralPath $localEngine -Destination $js -Force" in code
    assert code.index("if (Test-Path -LiteralPath $localEngine") < code.index("Invoke-WebRequest -Uri $url -OutFile $js")
    assert "if ($digest -cne $summary.sourcePinnedHash) { throw 'PINNED_B143_HASH_MISMATCH' }" in code
    assert "LOCAL_SHA256_VERIFIED_COPY" in code
    assert "NETWORK_SHA256_VERIFIED_DOWNLOAD" in code
