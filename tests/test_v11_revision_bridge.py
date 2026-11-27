"""No actual model, world, process, or saved revision is touched."""
import hashlib
import json
from pathlib import Path

import pytest

from app.core import SCHEMA, StudioError
from app.v11_revision_bridge import generate_revision_draft, MODEL, DIGEST


def test_revision_bridge_preserves_base_and_checks_receipt(tmp_path):
    base = tmp_path / 'base'
    base.mkdir()
    out = tmp_path / 'next'
    head = 'a' * 40
    instruction = '入口を明るくして使いやすくしてください。'
    sha = lambda x: hashlib.sha256(x).hexdigest()
    raw = b'{"schema":"original authored plan"}'
    brief = sha(instruction.encode())
    source = 'b' * 64
    geometry = 'c' * 64
    new_source = 'd' * 64
    new_geometry = 'e' * 64
    base_plan = 'f' * 64
    wire = '1' * 64
    def write(path, value):
        path.write_text(json.dumps(value), encoding='utf-8')
    def design(title):
        return {'schema': SCHEMA, 'designOnly': True, 'id': 'sample', 'title': title,
                'brief': 'test', 'bounds': {'width': 12, 'height': 12, 'depth': 12},
                'elements': [{'id': 'base', 'shape': 'box', 'mode': 'add', 'material': 'stone',
                              'origin': [2, 1, 2], 'size': [8, 3, 8]}]}
    def scene(s, g):
        return {'sourceSha256': s, 'geometrySha256': g, 'designOnly': True,
                'readiness': 'NOT_PREPARED', 'worldContacts': 0, 'worldCoordinates': None,
                'stats': {'constructionApproved': False}, 'BuildPlan': {'operations': []}}
    (base / 'MODEL_RESPONSE_ORIGINAL.txt').write_bytes(raw)
    write(base / 'RUN_REPORT.json', {'sourceVerified': True, 'model': MODEL,
        'sourceSha256': source, 'geometrySha256': geometry, 'originalSha256': sha(raw)})
    write(base / 'B143_SOURCE_REVIEW_ONLY.json', design('base'))
    current = scene(source, geometry)
    calls = []
    prepared = {'schema': 'r125-studio-revision-request-v1', 'mode': 'revision',
        'headSha': head, 'model': MODEL, 'briefSha256': brief, 'requestSha256': wire,
        'basePlanSha256': base_plan, 'sourceDiagnosisSha256': None, 'modelPostCount': 0,
        'endpoint': 'http://127.0.0.1:11435/v1/chat/completions', 'maxOutputTokens': 4096}
    bad = False
    def invoke(args):
        calls.append(args)
        if args[0] == '--prepare-studio-revision':
            return prepared
        assert args[:2] == ['--studio-revision', wire]
        out.mkdir()
        write(out / 'RUN_REPORT.json', {'sourceVerified': True, 'model': MODEL,
            'requestSha256': brief, 'revisionBasePlanSha256': base_plan, 'revisionDiagnosisSha256': None,
            'sourceSha256': new_source, 'geometrySha256': new_geometry})
        (out / 'REQUEST_ORIGINAL.txt').write_bytes(instruction.encode())
        (out / 'REVISION_BASE_PLAN.json').write_bytes(raw)
        write(out / 'B143_SOURCE_REVIEW_ONLY.json', design('revised'))
        return {**prepared, 'schema': 'r125-studio-owned-result-v1',
            'status': 'SOURCE_READY_REVIEW_ONLY', 'modelDigest': DIGEST, 'modelPostCount': 1,
            'sourceVerified': True, 'loadedModelObserved': True, 'unloadConfirmed': True,
            'ownedServiceStopConfirmed': True, 'monitorStopConfirmed': True, 'projectLockReleased': not bad,
            'automaticRetries': 0, 'worldContacts': 0, 'worldWrites': 0, 'artStatus': 'NOT_REVIEWED',
            'constructionApproved': False, 'executionAllowed': False, 'BuildPlan': {'operations': []},
            'gates': ['BLOCKED'] * 9, 'sourceSha256': new_source, 'geometrySha256': new_geometry,
            'runPath': str(out)}
    def compile_source(engine, doc):
        return current if doc['title'] == 'base' else scene(new_source, new_geometry)
    kwargs = dict(base_run=base, current_scene=current, instruction=instruction, output_dir=out,
                  engine=Path('unused'), head=head, invoke=invoke, compile_source=compile_source)
    draft, result = generate_revision_draft(**kwargs)
    assert draft['title'] == 'revised'
    assert result['geometrySha256'] == new_geometry
    assert len(calls) == 2
    assert (base / 'MODEL_RESPONSE_ORIGINAL.txt').read_bytes() == raw
    import shutil
    shutil.rmtree(out)
    bad = True
    with pytest.raises(StudioError, match='RESULT_HOLD'):
        generate_revision_draft(**kwargs)
    assert len(calls) == 4
    shutil.rmtree(out)
    with pytest.raises(StudioError, match='CANCELLED'):
        generate_revision_draft(**kwargs, cancelled=lambda: True)
    assert len(calls) == 4
    assert not out.exists()
