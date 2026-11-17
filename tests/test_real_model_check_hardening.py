"""Release guards for running one real local inference in the existing verified EXE.

These are source-level tests; native PowerShell 5.1 execution still needs Windows.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_real_model_check_wait_is_bounded_and_does_not_restart_inference():
    code = (ROOT / 'RUN_REAL_MODEL_CHECK.ps1').read_text(encoding='ascii')
    assert 'Start-Process -FilePath $exe -ArgumentList $arguments' in code
    assert '-PassThru\n' in code
    assert '$proc.WaitForExit(1200000)' in code
    assert 'ACCEPTANCE_PROCESS_TIMEOUT' in code
    assert '$proc.Kill()' in code
    assert code.count('Start-Process -FilePath $exe') == 1
    assert 'PROCESS_STARTED_INFERENCE_UNCONFIRMED' in code
    assert "realInference = 'REQUEST_ATTEMPTED'" in code


def test_real_model_diagnostic_preserves_pre_inference_failures():
    code = (ROOT / 'RUN_REAL_MODEL_CHECK.ps1').read_text(encoding='ascii')
    assert "if (!$receipt.actualProviderRequestAttempted)" in code
    assert "realInference = 'NOT_ATTEMPTED'" in code
    assert 'The verified EXE stopped before it called the selected provider.' in code
    assert 'modelSelectionNote' in code and 'loadedInstanceIds' in code


def test_success_requires_verified_model_and_source_gui_receipt():
    code = (ROOT / 'RUN_REAL_MODEL_CHECK.ps1').read_text(encoding='ascii')
    for expected in (
        "receipt.schema -cne 'mab-studio-local-ai-gui-acceptance-v1'",
        '$receipt.provider -cne $provider',
        '$receipt.model -cne $inferenceModel',
        '$receipt.testDoubleUsed',
        '$receipt.worldWrites -ne 0',
        '$receipt.worldContacts -ne 0',
        '$receipt.minecraftEducationTested',
        '$receipt.constructionApproved',
        '$receipt.BuildPlan.operations',
        '!$receipt.sourceValid',
        '!$receipt.guiRendered',
        'GENUINE_MODEL_SUCCESS_PROOF_INCOMPLETE',
    ):
        assert expected in code
    assert 'RUN_WINDOWS_NATIVE_QA.bat once' in code
    assert '/api/v1/models/load' not in code
    assert '/api/v1/models/unload' not in code
    assert '--test-double' not in code
