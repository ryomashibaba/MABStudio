"""Offline safety checks for the no-build real-model Windows acceptance flow."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_real_check_uses_previously_verified_exe_without_building():
    s=(ROOT/'RUN_REAL_MODEL_CHECK.ps1').read_text(encoding='ascii')
    assert 'PASS_WINDOWS_NATIVE_SOURCE_AND_TEST_DOUBLE_ONLY' in s
    assert 'Get-FileHash -Algorithm SHA256' in s
    assert 'Expand-Archive -LiteralPath $selected' in s
    assert '--acceptance' in s and '--provider' in s and '--model' in s
    assert '--test-double' not in s
    assert 'build_windows.bat' not in s
    assert 'pip install' not in s and 'npm install' not in s
    assert 'worldWrites = 0' in s

def test_real_check_does_not_autoload_models_or_touch_world():
    s=(ROOT/'RUN_REAL_MODEL_CHECK.ps1').read_text(encoding='ascii')
    assert '/api/v1/models' in s and '/api/ps' in s
    assert 'MODEL_NOT_UNIQUELY_LOADED' in s
    assert 'OLLAMA_MODEL_NOT_RUNNING' in s
    assert 'Invoke-RestMethod -Method Get' in s
    for forbidden in ('/api/generate', '/api/v1/models/load', '/api/v1/models/unload',
                      '/wsserver', 'setblock', 'fill ', 'clone '):
        assert forbidden not in s
    assert 'REAL_MODEL_CHECK.json' in s
    assert 'Stopwatch' in s and 'elapsedSeconds' in s

def test_native_qa_always_marks_mock_only_and_explains_real_check():
    s=(ROOT/'RUN_WINDOWS_NATIVE_QA.ps1').read_text(encoding='utf-8-sig')
    assert "genuineModelInference = 'NOT_RUN'" in s
    assert 'PASS_WINDOWS_NATIVE_SOURCE_AND_TEST_DOUBLE_ONLY' in s
    assert 'RUN_REAL_MODEL_CHECK.bat' in s

def test_acceptance_records_inference_seconds_on_failure():
    s=(ROOT/'app/acceptance.py').read_text(encoding='utf-8')
    assert "report['providerRequestSeconds'] = round(time.monotonic() - llm_started, 2)" in s
    assert 'finally:' in s
