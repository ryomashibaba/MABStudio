"""Source-level Windows command verification; actual PowerShell 5.1/EXE is HOLD."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(name):
    return (ROOT/name).read_text(encoding='utf-8-sig')


def test_source_fingerprint_shared_by_native_qa_and_real_acceptance():
    helper = text('GET_STUDIO_SOURCE_FINGERPRINT.ps1')
    qa = text('RUN_WINDOWS_NATIVE_QA.ps1')
    real = text('RUN_REAL_MODEL_CHECK.ps1')
    assert 'function Get-StudioSourceFingerprint' in helper
    assert "'app'" in helper
    assert 'build_windows_native.bat' in helper
    assert 'prepare_education_readonly.bat' in helper
    assert '__pycache__|node_modules' in helper
    assert 'Get-StudioFileSha256 -Path' in helper
    assert 'Get-StudioSourceFingerprint -Root $source' in qa
    assert 'ISOLATED_STUDIO_SOURCE_FINGERPRINT_MISMATCH' in qa
    assert "sourceFingerprintSha256 = ''" in qa
    assert '[string]$qa.sourceFingerprintSha256 -cne $sourceFingerprint' in real
    assert 'FIND_MATCHING_SOURCE_PASS_EXE' in real
    assert 'NO_VERIFIED_EXE_ZIP' in real


def test_real_model_request_saves_only_native_rendered_source():
    real = text('RUN_REAL_MODEL_CHECK.ps1')
    acceptance = text('app/acceptance.py')
    assert '--save-to-studio' in real
    assert "guiRenderer -cne 'VTK_NATIVE_EMBEDDED'" in real
    assert '!$receipt.nativeScenePixelsVerified' in real
    assert '!$receipt.studioProject.saved' in real
    assert 'savedRevisionId' in real
    assert 'save_verified_design_in_studio(' in acceptance
    assert "scene.get('readiness') != 'NOT_PREPARED'" in acceptance
    assert "report['stage'] = 'SOURCE_PREVIEW_RENDER'" in acceptance
    assert acceptance.index("report['stage'] = 'SOURCE_PREVIEW_RENDER'") < acceptance.index("report['stage'] = 'STUDIO_PROJECT_SAVE'")


def test_single_launch_reuses_exact_build_or_runs_isolated_native_qa_first():
    acceptance = text('RUN_WINDOWS_ACCEPTANCE.ps1')
    assert 'Get-StudioSourceFingerprint -Root $PSScriptRoot' in acceptance
    assert '[string]$qa.sourceFingerprintSha256 -cne $sourceHash' in acceptance
    assert 'Get-FileHash -Algorithm SHA256 -LiteralPath $zipFile' in acceptance
    assert 'RUN_WINDOWS_NATIVE_QA.ps1' in acceptance
    assert 'RUN_REAL_MODEL_CHECK.ps1' in acceptance
    assert acceptance.index('RUN_WINDOWS_NATIVE_QA.ps1') < acceptance.index('RUN_REAL_MODEL_CHECK.ps1')
    assert 'Read-Host' not in acceptance
    assert 'build_windows.bat' not in acceptance
    assert 'pip install' not in acceptance
    assert 'Invoke-RestMethod -Method Post' not in acceptance
    assert 'setblock' not in acceptance
    assert 'exit 0' in acceptance and 'exit 1' in acceptance


def test_windows_control_scripts_are_crlf_and_ps51_compatible_encoding():
    for name in ('GET_STUDIO_SOURCE_FINGERPRINT.ps1', 'RUN_WINDOWS_NATIVE_QA.ps1',
                 'RUN_REAL_MODEL_CHECK.ps1', 'RUN_WINDOWS_ACCEPTANCE.ps1'):
        data = (ROOT/name).read_bytes()
        assert data.count(b'\n') == data.count(b'\r\n'), name
        assert data.endswith(b'\r\n'), name
        if not data.isascii():
            assert data.startswith(b'\xef\xbb\xbf')
