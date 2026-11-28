"""Read one completed V11 R125 run as an unapproved fixed-B143 candidate.

This module does not launch a runner, invoke a model, adopt ART, or approve
construction. The V11 originals stay in the private run directory.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile


def read_run(directory, *, load_design, compile_design,
             expected_prompt: str | None = None, expected_model: str | None = None):
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Invalid V11 run directory')

    def read(name, limit=1024 * 1024):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or invalid V11 file: ' + name)
        with path.open('rb') as stream:
            raw = stream.read(limit + 1)
        if not raw or len(raw) > limit:
            raise ValueError('Invalid V11 file size: ' + name)
        return raw

    def obj(raw):
        value = json.loads(raw.decode('utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Expected V11 JSON object')
        return value

    def sha(raw):
        return hashlib.sha256(raw).hexdigest()

    def require(ok, message):
        if not ok:
            raise ValueError(message)

    report_raw = read('RUN_REPORT.json', 300_000)
    report = obj(report_raw)
    require(report.get('schema') == 'r125-local-plan-one-shot-v1' and
            report.get('status') == 'B143_SOURCE_CANDIDATE_ART_HOLD' and
            report.get('sourceVerified') is True and
            report.get('artStatus') == 'NOT_REVIEWED' and
            report.get('constructionApproved') is False and
            report.get('originalEdited') is False and
            report.get('nineWorldGates') == ['BLOCKED'] * 9 and
            type(report.get('worldWrites')) is int and report['worldWrites'] == 0 and
            type(report.get('worldContacts')) is int and report['worldContacts'] == 0 and
            type(report.get('automaticRetries')) is int and report['automaticRetries'] == 0 and
            type(report.get('attemptLimit')) is int and report['attemptLimit'] == 1 and
            type(report.get('modelCallAttempts')) is int and report['modelCallAttempts'] == 1 and
            type(report.get('modelResponsesCaptured')) is int and report['modelResponsesCaptured'] == 1 and
            report.get('authorshipMode') == 'NEW_MODEL_AUTHORED_R125_RESPONSE' and
            report.get('revisionBasePlanSha256') is None,
            'Run is not a single fresh SOURCE candidate')

    prompt_raw = read('REQUEST_ORIGINAL.txt', 40_000)
    prompt = prompt_raw.decode('utf-8')
    original_raw = read('MODEL_RESPONSE_ORIGINAL.txt')
    model = report.get('model')
    require(isinstance(model, str) and bool(model.strip()) and
            (expected_prompt is None or prompt == expected_prompt) and
            (expected_model is None or model == expected_model) and
            sha(prompt_raw) == report.get('requestSha256') and
            sha(original_raw) == report.get('originalSha256'),
            'Request/model/original capture mismatch')
    obj(original_raw)

    diagnosis_raw = read('SOURCE_DIAGNOSIS.json', 300_000)
    diagnosis = obj(diagnosis_raw)
    require(diagnosis.get('sourceVerified') is True and
            diagnosis.get('status') == report['status'],
            'SOURCE diagnosis is not a passing candidate')
    if report.get('delegationSelected') is True:
        delegated = obj(read('DELEGATED_SOURCE_DIAGNOSIS.json', 300_000))
        require(delegated.get('originalSha256') == sha(original_raw) and
                delegated.get('sourceVerified') is True and
                delegated.get('diagnostic') == diagnosis,
                'Delegated SOURCE receipt mismatch')

    source_raw = read('B143_SOURCE_REVIEW_ONLY.json')
    with tempfile.TemporaryDirectory(prefix='studio-v11-import-') as temp:
        source_file = Path(temp) / 'B143_SOURCE_REVIEW_ONLY.json'
        source_file.write_bytes(source_raw)
        design = load_design(source_file)
    scene = compile_design(design)
    for key in ('sourceSha256', 'geometrySha256'):
        digest = scene.get(key)
        require(isinstance(digest, str) and len(digest) == 64 and
                all(char in '0123456789abcdef' for char in digest) and
                digest == report.get(key) == diagnosis.get(key),
                'Frozen B143 mismatch: ' + key)

    # Detect in-place replacement while the offline B143 compiler was running.
    require(read('RUN_REPORT.json', 300_000) == report_raw and
            read('B143_SOURCE_REVIEW_ONLY.json') == source_raw and
            read('SOURCE_DIAGNOSIS.json', 300_000) == diagnosis_raw and
            read('REQUEST_ORIGINAL.txt', 40_000) == prompt_raw and
            read('MODEL_RESPONSE_ORIGINAL.txt') == original_raw,
            'Run changed during import')
    return {'design': design, 'scene': scene, 'report': report,
            'diagnosis': diagnosis, 'original': original_raw.decode('utf-8'),
            'request': prompt, 'reportSha256': sha(report_raw),
            'sourceFileSha256': sha(source_raw), 'artAdopted': False,
            'constructionApproved': False,
            'provenance': 'V11_REPORT_NOT_INDEPENDENTLY_ATTESTED'}
