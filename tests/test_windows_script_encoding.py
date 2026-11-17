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
    assert 'npm install -g' not in batch
