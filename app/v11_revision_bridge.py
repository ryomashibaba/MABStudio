"""V11 revision worker adapter for the existing Studio review flow.

No model server management, approvals, GUI or saved-project writes here.
The caller supplies the existing owned CLI invocation (including its PRIVATE
environment and cancellation handling). Only the pinned compiler validates
B143. A result is an unapproved draft, never a saved revision or construction.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from app.core import StudioError, run_bridge
from app.v11_source_import import read_external_b143_source

HEAD_RE = re.compile(r'[0-9a-f]{40}\Z')
SHA_RE = re.compile(r'[0-9a-f]{64}\Z')
MODEL = 'qwen3:14b'
DIGEST = 'bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8'


def _require(ok, reason):
    if not ok:
        raise StudioError(reason)


def _read(path, limit=1024 * 1024):
    try:
        _require(path.is_file() and not path.is_symlink(), 'V11_REVISION_FILE_INVALID')
        with path.open('rb') as handle:
            raw = handle.read(limit + 1)
        _require(0 < len(raw) <= limit, 'V11_REVISION_FILE_SIZE')
        return raw
    except OSError as exc:
        raise StudioError('V11_REVISION_FILE_UNAVAILABLE') from exc


def _json(path):
    try:
        value = json.loads(_read(path).decode('utf-8'))
    except (UnicodeError, ValueError) as exc:
        raise StudioError('V11_REVISION_JSON_INVALID') from exc
    _require(isinstance(value, dict), 'V11_REVISION_JSON_INVALID')
    return value


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash_valid(value):
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def _safe_scene(scene):
    return (isinstance(scene, dict) and scene.get('designOnly') is True
            and scene.get('readiness') == 'NOT_PREPARED'
            and scene.get('worldContacts') == 0
            and scene.get('worldCoordinates') is None
            and scene.get('stats', {}).get('constructionApproved') is False
            and scene.get('BuildPlan', {}).get('operations') == []
            and all(_hash_valid(scene.get(k)) for k in ('sourceSha256', 'geometrySha256')))


def generate_revision_draft(*, base_run: Path, current_scene: dict, instruction: str,
                            output_dir: Path, engine: Path, head: str,
                            invoke, cancelled=lambda: False, compile_source=run_bridge):
    """Return (design, scene) for the existing unapproved revision preview.

    invoke(argv) must be the existing trusted owned-worker CLI adapter, return
    its parsed JSON receipt and enforce cancellation and exact-head approval.
    It is called once for preparation and once for execution, with no retry.
    The current active project identity must still be checked by the UI's
    existing completion callback before accepting the draft.
    """
    _require(isinstance(head, str) and HEAD_RE.fullmatch(head), 'V11_REVISION_HEAD_INVALID')
    _require(isinstance(instruction, str) and 8 <= len(instruction.strip()) <= 8000,
             'V11_REVISION_INSTRUCTION_INVALID')
    instruction = instruction.strip()
    base_run, output_dir = Path(base_run), Path(output_dir)
    _require(base_run.is_absolute() and base_run.is_dir() and not base_run.is_symlink(),
             'V11_REVISION_BASE_RUN_INVALID')
    _require(output_dir.is_absolute() and not output_dir.exists() and not output_dir.is_symlink()
             and base_run.resolve() not in output_dir.resolve().parents,
             'V11_REVISION_OUTPUT_MUST_BE_NEW')
    _require(_safe_scene(current_scene), 'V11_REVISION_CURRENT_SOURCE_INVALID')
    report = _json(base_run / 'RUN_REPORT.json')
    keys = ('sourceSha256', 'geometrySha256')
    _require(report.get('sourceVerified') is True and report.get('model') == MODEL
             and all(report.get(k) == current_scene[k] for k in keys),
             'V11_REVISION_BASE_SOURCE_MISMATCH')
    base_design = read_external_b143_source(base_run / 'B143_SOURCE_REVIEW_ONLY.json')
    base_scene = compile_source(engine, base_design)
    _require(_safe_scene(base_scene) and all(base_scene[k] == current_scene[k] for k in keys),
             'V11_REVISION_BASE_REPLAY_MISMATCH')
    plan_path = base_run / 'MODEL_RESPONSE_ORIGINAL.txt'
    original = _read(plan_path)
    _require(_sha(original) == report.get('originalSha256'), 'V11_REVISION_ORIGINAL_MISMATCH')
    # Normalization is the existing model adapter's verified representation,
    # not a new host-authored plan. Prefer it only when the report attests it.
    normalization = report.get('normalization')
    if isinstance(normalization, dict) and normalization.get('applied') is True:
        plan_path = base_run / 'MODEL_RESPONSE_NORMALIZED_PLAN.json'
        plan_bytes = _read(plan_path)
        _require(_sha(plan_bytes) == report.get('normalizedPlanSha256'),
                 'V11_REVISION_NORMALIZED_PLAN_MISMATCH')
    else:
        plan_bytes = original
    _require(not cancelled(), 'V11_REVISION_CANCELLED')
    args = ['--model', MODEL, '--prompt', instruction, '--out', str(output_dir),
            '--revise-plan', str(plan_path)]
    prepared = invoke(['--prepare-studio-revision', *args])
    brief_sha = _sha(instruction.encode('utf-8'))
    _require(isinstance(prepared, dict)
             and prepared.get('schema') == 'r125-studio-revision-request-v1'
             and prepared.get('mode') == 'revision' and prepared.get('headSha') == head
             and prepared.get('model') == MODEL and prepared.get('briefSha256') == brief_sha
             and prepared.get('endpoint') == 'http://127.0.0.1:11435/v1/chat/completions'
             and prepared.get('maxOutputTokens') == 4096
             and type(prepared.get('modelPostCount')) is int and prepared['modelPostCount'] == 0
             and _hash_valid(prepared.get('requestSha256')) and _hash_valid(prepared.get('basePlanSha256'))
             and 'sourceDiagnosisSha256' in prepared and prepared['sourceDiagnosisSha256'] is None,
             'V11_REVISION_PREPARE_MISMATCH')
    _require(not cancelled(), 'V11_REVISION_CANCELLED')
    _require(_read(plan_path) == plan_bytes and _read(base_run / 'MODEL_RESPONSE_ORIGINAL.txt') == original,
             'V11_REVISION_BASE_CHANGED')
    receipt = invoke(['--studio-revision', prepared['requestSha256'], *args])
    _require(not cancelled(), 'V11_REVISION_CANCELLED')
    _require(isinstance(receipt, dict)
             and receipt.get('schema') == 'r125-studio-owned-result-v1'
             and receipt.get('mode') == 'revision'
             and receipt.get('status') == 'SOURCE_READY_REVIEW_ONLY'
             and receipt.get('modelDigest') == DIGEST
             and all(receipt.get(k) == prepared[k] for k in
                     ('headSha', 'model', 'briefSha256', 'requestSha256', 'basePlanSha256', 'sourceDiagnosisSha256'))
             and all(receipt.get(k) is True for k in ('sourceVerified', 'loadedModelObserved',
                     'unloadConfirmed', 'ownedServiceStopConfirmed', 'monitorStopConfirmed', 'projectLockReleased'))
             and all(type(receipt.get(k)) is int and receipt[k] == v for k, v in
                     (('modelPostCount', 1), ('automaticRetries', 0), ('worldContacts', 0), ('worldWrites', 0)))
             and receipt.get('artStatus') == 'NOT_REVIEWED'
             and receipt.get('constructionApproved') is False and receipt.get('executionAllowed') is False
             and receipt.get('BuildPlan') == {'operations': []}
             and receipt.get('gates') == ['BLOCKED'] * 9
             and all(_hash_valid(receipt.get(k)) for k in keys),
             'V11_REVISION_RESULT_HOLD')
    _require(isinstance(receipt.get('runPath'), str)
             and Path(receipt['runPath']).resolve() == output_dir.resolve()
             and output_dir.is_dir() and not output_dir.is_symlink(),
             'V11_REVISION_RUN_PATH_MISMATCH')
    saved = _json(output_dir / 'RUN_REPORT.json')
    _require(saved.get('sourceVerified') is True and saved.get('model') == MODEL
             and saved.get('requestSha256') == brief_sha
             and saved.get('revisionBasePlanSha256') == prepared['basePlanSha256']
             and 'revisionDiagnosisSha256' in saved and saved['revisionDiagnosisSha256'] is None
             and all(saved.get(k) == receipt[k] for k in keys)
             and _sha(_read(output_dir / 'REQUEST_ORIGINAL.txt')) == brief_sha,
             'V11_REVISION_SAVED_PROVENANCE_MISMATCH')
    _require(_read(output_dir / 'REVISION_BASE_PLAN.json') == plan_bytes
             and _read(plan_path) == plan_bytes
             and _read(base_run / 'MODEL_RESPONSE_ORIGINAL.txt') == original,
             'V11_REVISION_BASE_CHANGED')
    design = read_external_b143_source(output_dir / 'B143_SOURCE_REVIEW_ONLY.json')
    scene = compile_source(engine, design)
    _require(_safe_scene(scene) and all(scene[k] == receipt[k] for k in keys),
             'V11_REVISION_SOURCE_REPLAY_MISMATCH')
    _require(not cancelled(), 'V11_REVISION_CANCELLED')
    return design, scene
