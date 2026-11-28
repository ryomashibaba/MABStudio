import hashlib
import json

import pytest

from app.examples import example_library
from app.v11_run_import import read_run


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _run(root):
    design = example_library()
    source = json.dumps(design, ensure_ascii=False).encode('utf-8')
    prompt = '小さな図書館を設計してください。'
    prompt_raw = prompt.encode('utf-8')
    original = json.dumps({'plan': {'levels': []}}).encode('utf-8')
    scene = {'sourceSha256': 'a' * 64, 'geometrySha256': 'b' * 64}
    diagnosis = {'sourceVerified': True, 'status': 'B143_SOURCE_CANDIDATE_ART_HOLD',
                 **scene}
    report = {
        'schema': 'r125-local-plan-one-shot-v1',
        'status': 'B143_SOURCE_CANDIDATE_ART_HOLD',
        'sourceVerified': True, 'artStatus': 'NOT_REVIEWED',
        'constructionApproved': False, 'originalEdited': False,
        'nineWorldGates': ['BLOCKED'] * 9,
        'worldWrites': 0, 'worldContacts': 0, 'automaticRetries': 0,
        'attemptLimit': 1, 'modelCallAttempts': 1, 'modelResponsesCaptured': 1,
        'authorshipMode': 'NEW_MODEL_AUTHORED_R125_RESPONSE',
        'revisionBasePlanSha256': None,
        'requestSha256': _sha(prompt_raw), 'originalSha256': _sha(original),
        'model': 'qwen3:14b', **scene,
    }
    (root / 'REQUEST_ORIGINAL.txt').write_bytes(prompt_raw)
    (root / 'MODEL_RESPONSE_ORIGINAL.txt').write_bytes(original)
    (root / 'B143_SOURCE_REVIEW_ONLY.json').write_bytes(source)
    (root / 'SOURCE_DIAGNOSIS.json').write_text(json.dumps(diagnosis), encoding='utf-8')
    (root / 'RUN_REPORT.json').write_text(json.dumps(report), encoding='utf-8')
    return design, report


def test_valid_run_is_compiled_as_unapproved_candidate_without_mutation(tmp_path):
    design, report = _run(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = read_run(
        tmp_path,
        load_design=lambda path: json.loads(path.read_text(encoding='utf-8')),
        compile_design=lambda value: {'sourceSha256': 'a' * 64,
                                      'geometrySha256': 'b' * 64,
                                      'design': value})
    assert result['design'] == design
    assert result['report'] == report
    assert result['artAdopted'] is False
    assert result['constructionApproved'] is False
    assert result['provenance'] == 'V11_REPORT_NOT_INDEPENDENTLY_ATTESTED'
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize('change', [
    lambda report: report.update(worldWrites=1),
    lambda report: report.update(attemptLimit=2),
    lambda report: report.update(constructionApproved=True),
    lambda report: report.update(status='HOLD_MODEL_OR_SOURCE'),
])
def test_hold_or_unsafe_run_is_rejected(tmp_path, change):
    _run(tmp_path)
    report_path = tmp_path / 'RUN_REPORT.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    change(report)
    report_path.write_text(json.dumps(report), encoding='utf-8')
    with pytest.raises(ValueError):
        read_run(tmp_path, load_design=lambda p: json.loads(p.read_text(encoding='utf-8')),
                 compile_design=lambda d: {'sourceSha256': 'a' * 64,
                                           'geometrySha256': 'b' * 64})


def test_original_capture_and_b143_geometry_must_match(tmp_path):
    _run(tmp_path)
    with (tmp_path / 'MODEL_RESPONSE_ORIGINAL.txt').open('ab') as f:
        f.write(b' tampered')
    with pytest.raises(ValueError, match='capture mismatch'):
        read_run(tmp_path, load_design=lambda p: json.loads(p.read_text(encoding='utf-8')),
                 compile_design=lambda d: {'sourceSha256': 'a' * 64,
                                           'geometrySha256': 'b' * 64})

    _run(tmp_path)
    with pytest.raises(ValueError, match='Frozen B143 mismatch'):
        read_run(tmp_path, load_design=lambda p: json.loads(p.read_text(encoding='utf-8')),
                 compile_design=lambda d: {'sourceSha256': '0' * 64,
                                           'geometrySha256': 'b' * 64})

