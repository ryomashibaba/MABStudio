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
    assert 'LM_STUDIO_NO_LOADED_LLM' in s
    assert 'LM_STUDIO_MULTIPLE_INSTANCES_FOR_SELECTED_MODEL' in s
    assert 'LM_STUDIO_AMBIGUOUS_LOADED_MODELS' in s
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


def test_real_check_resolves_loaded_instance_ids_instead_of_saved_picker_ids():
    s=(ROOT/'RUN_REAL_MODEL_CHECK.ps1').read_text(encoding='ascii')
    # Native API keys and already-loaded instance IDs are separate identifiers.
    assert 'EXACT_LOADED_KEY_OR_INSTANCE_ID' in s
    assert 'SELECTED_MODEL_MISMATCH_HOLD' in s
    assert 'LM_STUDIO_SELECTED_MODEL_NOT_LOADED' in s
    assert "[string]$entry.type -cne 'llm'" in s
    assert "[string]$instance.id" in s
    assert '$_.id -ceq $model -or $_.key -ceq $model' in s
    assert '$loadedCandidates.Count -eq 1' in s
    assert 'saved GUI model differs from the sole loaded LLM' not in s
    assert '+ $inferenceModel +' in s and '--model' in s
    assert 'savedModel' in s and 'resolvedModel' in s
    assert 'modelSelectionMethod' in s
    # No model may be loaded via the diagnostics; the only POST remains in the EXE.
    assert '/api/v1/models/load' not in s
    assert 'Invoke-RestMethod -Method Post' not in s

def test_model_check_script_is_ps51_plain_ascii_and_preserves_strict_guards():
    raw=(ROOT/'RUN_REAL_MODEL_CHECK.ps1').read_bytes()
    assert raw.isascii() and b'\r\n' in raw
    assert b'\n' not in raw.replace(b'\r\n', b'')
    s=raw.decode('ascii')
    assert 'NO_VERIFIED_EXE_ZIP' in s and 'Get-FileHash -Algorithm SHA256' in s
    assert 'GENUINE_MODEL_RECEIPT_INCONSISTENT' in s
    assert '$summary.realInference' in s
