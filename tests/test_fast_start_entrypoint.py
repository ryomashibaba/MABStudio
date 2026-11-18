"""Regression for user startup: use accepted EXE, never run the acceptance suite."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_fast_start_is_windows_51_plain_ascii_and_does_not_rebuild_or_run_ai():
    for name in ('OPEN_MAB_STUDIO.bat', 'OPEN_MAB_STUDIO.ps1'):
        raw = (ROOT / name).read_bytes()
        assert raw.isascii(), name
        assert b'\r\n' in raw and b'\n' not in raw.replace(b'\r\n', b''), name
    s = (ROOT / 'OPEN_MAB_STUDIO.ps1').read_text(encoding='ascii')
    assert "PASS_WINDOWS_NATIVE_SOURCE_AND_TEST_DOUBLE_ONLY" in s
    assert 'Get-StudioSourceFingerprint -Root $PSScriptRoot' in s
    assert 'FAST_START_RECEIPT.json' in s
    assert 'previewZipSha256' in s
    assert 'Get-FileHash -Algorithm SHA256' in s
    assert 'Expand-Archive' in s
    assert 'Start-Process -FilePath $exe' in s
    assert 'CACHED_EXE_INVALID' in s
    assert 'GUI_EXITED_EARLY' in s
    for forbidden in ('build_windows', 'pip install', 'npm install',
                      'Invoke-RestMethod', '--acceptance', '--smoke',
                      'RUN_WINDOWS_ACCEPTANCE', '--save-to-studio',
                      'MinecraftAIBuilder/main'):
        assert forbidden not in s


def test_real_check_archive_name_matches_actual_native_qa_archive():
    native = (ROOT / 'RUN_WINDOWS_NATIVE_QA.ps1').read_text(encoding='utf-8-sig')
    real = (ROOT / 'RUN_REAL_MODEL_CHECK.ps1').read_text(encoding='ascii')
    fast = (ROOT / 'OPEN_MAB_STUDIO.ps1').read_text(encoding='ascii')
    name = 'MABStudio-Windows-Native-Preview.zip'
    assert name in native and name in real and name in fast
    assert 'MABStudio-Windows-Safe-Preview.zip' not in real


def test_acceptance_checks_model_before_expensive_build():
    code = (ROOT / 'RUN_WINDOWS_ACCEPTANCE.ps1').read_text(encoding='utf-8-sig')
    assert code.index('MODEL_NOT_LOADED') < code.index('RUN_WINDOWS_NATIVE_QA.ps1')
    assert code.index('Invoke-RestMethod -Method Get') < code.index('RUN_WINDOWS_NATIVE_QA.ps1')
    assert code.count('RUN_REAL_MODEL_CHECK.ps1') == 1
    assert 'Invoke-RestMethod -Method Post' not in code
