import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile

import pytest

from app.v11_studio_generation import (
    MODEL, MODEL_DIGEST, V11_HEAD, V11GenerationError, default_private_module,
    default_strata_repo, default_v11_repo, prepare_new_request,
    validate_source_ready_receipt, _studio_new_approval,
)
from app.examples import example_library


def test_exact_head_post_free_studio_request_preparation():
    from app.core import node_binary

    prompt = '小さな地域図書館を設計してください。入口から閲覧スペースへ通れ、本棚と受付を備えてください。'
    request, out_dir, _env = prepare_new_request(
        node_exe=node_binary(), prompt=prompt, model=MODEL,
        repo=default_v11_repo(), private_module=default_private_module(),
        strata_repo=default_strata_repo())

    assert request['headSha'] == V11_HEAD
    assert request['model'] == MODEL
    assert request['briefSha256'] == hashlib.sha256(prompt.encode('utf-8')).hexdigest()
    assert request['modelPostCount'] == 0
    assert len(request['requestSha256']) == 64
    assert out_dir.parent.name == 'Studio-V40.1-Runs'
    assert not out_dir.exists()


def test_only_settled_source_ready_receipt_is_accepted():
    with tempfile.TemporaryDirectory(
            prefix='v11-studio-receipt-',
            dir=Path(default_private_module()).parents[1]) as temp:
        _assert_receipt_gates(Path(temp))


def _assert_receipt_gates(tmp_path):
    request = {'model': MODEL, 'briefSha256': 'a' * 64,
               'requestSha256': 'b' * 64}
    ready = {
        'schema': 'r125-studio-owned-result-v1', 'mode': 'new',
        'status': 'SOURCE_READY_REVIEW_ONLY', 'headSha': V11_HEAD,
        'model': MODEL, 'modelDigest': MODEL_DIGEST,
        'briefSha256': request['briefSha256'],
        'requestSha256': request['requestSha256'], 'modelPostCount': 1,
        'automaticRetries': 0, 'loadedModelObserved': True,
        'unloadConfirmed': True, 'ownedServiceStopConfirmed': True,
        'monitorStopConfirmed': True, 'projectLockReleased': True,
        'sourceVerified': True, 'sourceSha256': 'c' * 64,
        'geometrySha256': 'd' * 64, 'worldContacts': 0, 'worldWrites': 0,
        'constructionApproved': False, 'executionAllowed': False,
        'artStatus': 'NOT_REVIEWED', 'runPath': str(tmp_path),
    }
    assert validate_source_ready_receipt(
        ready, request=request, out_dir=tmp_path) == tmp_path.resolve()

    for key, value in (('status', 'HOLD'), ('modelPostCount', 0),
                       ('modelDigest', 'f' * 64),
                       ('unloadConfirmed', False), ('projectLockReleased', False),
                       ('sourceVerified', False), ('worldWrites', 1)):
        bad = dict(ready, **{key: value})
        with pytest.raises(V11GenerationError):
            validate_source_ready_receipt(bad, request=request, out_dir=tmp_path)


def test_studio_new_action_routes_to_v11_without_legacy_llm(monkeypatch):
    import app.main as main

    studio = object.__new__(main.Studio)
    studio.pending_revision = None
    studio.prompt = SimpleNamespace(get=lambda *_args: '日本語の建築依頼です。')
    studio.prompt_mode = SimpleNamespace(get=lambda: 'new')
    studio.llm_provider = SimpleNamespace(get=lambda: 'Ollama')
    studio.model = SimpleNamespace(get=lambda: MODEL)
    studio.manager = SimpleNamespace(active_path=Path('pinned-b143'))
    routed = []
    studio._generate_v11_studio_new = lambda prompt, model: routed.append((prompt, model))
    monkeypatch.setattr(main, 'ask_llm', lambda *_a, **_k: pytest.fail('legacy model path used'))

    studio.generate()

    assert routed == [('日本語の建築依頼です。', MODEL)]


def test_offline_orchestration_invokes_v11_entry_and_receipt_gate(monkeypatch):
    import app.v11_studio_generation as bridge

    prompt = '日本語で小さな図書館を設計してください。'
    request_sha = '1' * 64
    brief_sha = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
    calls = []

    with tempfile.TemporaryDirectory(
            prefix='v11-studio-orchestration-',
            dir=Path(default_private_module()).parents[1]) as temp:
        parent = Path(temp)
        monkeypatch.setattr(bridge, 'private_run_parent', lambda: parent)

        def fake_runner(argv, *, env, cwd):
            calls.append((list(argv), dict(env), Path(cwd)))
            if len(calls) == 1:
                return 0, {
                    'schema': 'r125-studio-new-request-v1', 'mode': 'new',
                    'headSha': V11_HEAD, 'model': MODEL,
                    'endpoint': 'http://127.0.0.1:11435/v1/chat/completions',
                    'maxOutputTokens': 4096,
                    'briefSha256': brief_sha, 'requestSha256': request_sha,
                    'modelPostCount': 0,
                }
            out_dir = Path(argv[-1])
            out_dir.mkdir()
            return 0, {
                'schema': 'r125-studio-owned-result-v1', 'mode': 'new',
                'status': 'SOURCE_READY_REVIEW_ONLY', 'headSha': V11_HEAD,
                'model': MODEL, 'modelDigest': MODEL_DIGEST,
                'briefSha256': brief_sha,
                'requestSha256': request_sha, 'modelPostCount': 1,
                'automaticRetries': 0, 'loadedModelObserved': True,
                'unloadConfirmed': True, 'ownedServiceStopConfirmed': True,
                'monitorStopConfirmed': True, 'projectLockReleased': True,
                'sourceVerified': True, 'sourceSha256': '2' * 64,
                'geometrySha256': '3' * 64, 'worldContacts': 0, 'worldWrites': 0,
                'constructionApproved': False, 'executionAllowed': False,
                'artStatus': 'NOT_REVIEWED', 'runPath': str(out_dir),
            }

        monkeypatch.setattr(bridge, '_run_json', fake_runner)
        receipt, run_dir = bridge.run_new_generation(
            node_exe='node.exe', prompt=prompt, model=MODEL,
            repo=default_v11_repo(), private_module=default_private_module(),
            strata_repo=default_strata_repo())

    assert len(calls) == 2
    assert calls[0][0][1].endswith('r125-local-architecture-plan-owned.mjs')
    assert calls[0][0][2] == '--prepare-studio-new'
    assert calls[1][0][2] == '--studio-new'
    assert calls[1][0][3] == request_sha
    assert 'MAB_R125_STUDIO_NEW_APPROVAL' not in calls[0][1]
    assert calls[1][1]['MAB_R125_STUDIO_NEW_APPROVAL'] == (
        f'APPROVE ONE R125 QWEN3:14B STUDIO NEW POST {request_sha} ON HEAD {V11_HEAD}')
    assert '--prompt' in calls[1][0] and prompt in calls[1][0]
    assert receipt['status'] == 'SOURCE_READY_REVIEW_ONLY'
    assert run_dir.parent.name.startswith('v11-studio-orchestration-')


def test_each_japanese_generate_action_gets_its_own_exact_approval():
    from app.core import node_binary

    prompts = (
        '駅前に木造の小さな図書館を設計してください。',
        '川辺に展望デッキのある石造りの資料館を設計してください。',
    )
    approvals = []
    prepared_requests = []
    for prompt in prompts:
        request, _out_dir, child_env = prepare_new_request(
            node_exe=node_binary(), prompt=prompt, model=MODEL,
            repo=default_v11_repo(), private_module=default_private_module(),
            strata_repo=default_strata_repo())
        prepared_requests.append(request)
        assert request['schema'] == 'r125-studio-new-request-v1'
        assert request['headSha'] == V11_HEAD and request['model'] == MODEL
        assert request['briefSha256'] == hashlib.sha256(prompt.encode('utf-8')).hexdigest()
        assert request['modelPostCount'] == 0
        assert 'MAB_R125_STUDIO_NEW_APPROVAL' not in child_env
        approvals.append(_studio_new_approval(request))

    assert approvals[0] != approvals[1]
    assert prepared_requests[0]['requestSha256'] != prepared_requests[1]['requestSha256']
    assert V11_HEAD in approvals[0] and V11_HEAD in approvals[1]
    assert approvals[0].endswith(V11_HEAD) and approvals[1].endswith(V11_HEAD)


def test_cancel_after_post_free_preparation_starts_no_v11_post(monkeypatch):
    import threading
    import app.v11_studio_generation as bridge

    prompt = '駅前に木造の小さな図書館を設計してください。'
    cancel_event = threading.Event()
    calls = []

    def prepare(**_kwargs):
        cancel_event.set()
        return ({
            'schema': 'r125-studio-new-request-v1', 'mode': 'new',
            'headSha': V11_HEAD, 'model': MODEL,
            'endpoint': 'http://127.0.0.1:11435/v1/chat/completions',
            'maxOutputTokens': 4096, 'modelPostCount': 0,
            'briefSha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
            'requestSha256': 'a' * 64,
        }, Path('unused-run'), {'MAB_HEAD': V11_HEAD})

    monkeypatch.setattr(bridge, 'prepare_new_request', prepare)
    monkeypatch.setattr(bridge, '_run_json', lambda *a, **k: calls.append(a))
    with pytest.raises(V11GenerationError, match='CANCELLED_BEFORE_STUDIO_NEW_POST'):
        bridge.run_new_generation(node_exe='node.exe', prompt=prompt,
                                  bounded=True,
                                  cancel_event=cancel_event)
    assert calls == []


def test_bounded_studio_mode_uses_bounded_cli_and_unproven_receipt(monkeypatch):
    import app.v11_studio_generation as bridge

    prompt = '水辺に小さな観測資料館を設計してください。'
    brief_sha = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
    request_sha = 'e' * 64
    calls = []
    run_dir_exists = False
    with tempfile.TemporaryDirectory(
            prefix='v11-studio-bounded-',
            dir=Path(default_private_module()).parents[1]) as temp:
        parent = Path(temp)
        monkeypatch.setattr(bridge, 'private_run_parent', lambda: parent)
        monkeypatch.setattr(bridge, 'prepare_new_request', lambda **_kwargs: ({
            'schema': 'r125-studio-new-request-v1', 'mode': 'new',
            'headSha': V11_HEAD, 'model': MODEL,
            'endpoint': 'http://127.0.0.1:11435/v1/chat/completions',
            'maxOutputTokens': 4096, 'briefSha256': brief_sha,
            'requestSha256': request_sha, 'modelPostCount': 0,
        }, parent / 'private-run', {'MAB_HEAD': V11_HEAD}))

        def fake_runner(argv, *, env, cwd):
            calls.append((list(argv), dict(env), Path(cwd)))
            out_dir = Path(argv[-1])
            out_dir.mkdir()
            return 0, {
                'schema': 'r125-studio-owned-result-v1', 'mode': 'new',
                'status': 'SOURCE_READY_REVIEW_ONLY', 'headSha': V11_HEAD,
                'model': MODEL, 'modelDigest': MODEL_DIGEST,
                'briefSha256': brief_sha, 'requestSha256': request_sha,
                'modelPostCount': 1, 'automaticRetries': 0,
                'loadedModelObserved': True, 'unloadConfirmed': True,
                'ownedServiceStopConfirmed': True, 'monitorStopConfirmed': True,
                'projectLockReleased': True, 'sourceVerified': True,
                'sourceSha256': '2' * 64, 'geometrySha256': '3' * 64,
                'worldContacts': 0, 'worldWrites': 0, 'constructionApproved': False,
                'executionAllowed': False, 'artStatus': 'NOT_REVIEWED',
                'resourceMode': 'BOUNDED_REAL_REQUEST_TRIAL',
                'resourceSafetyCertified': False, 'inferenceMemoryPeakCalibrated': False,
                'resourceEvidenceSha256': '4' * 64, 'runPath': str(out_dir),
            }

        monkeypatch.setattr(bridge, '_run_json', fake_runner)
        receipt, run_dir = bridge.run_new_generation(
            node_exe='node.exe', prompt=prompt, model=MODEL, bounded=True,
            repo=default_v11_repo(), private_module=default_private_module(),
            strata_repo=default_strata_repo())
        run_dir_exists = run_dir.is_dir()

    assert len(calls) == 1
    assert calls[0][0][2] == '--studio-new-bounded'
    assert calls[0][1]['MAB_R125_STUDIO_NEW_APPROVAL'].endswith(V11_HEAD)
    assert receipt['resourceSafetyCertified'] is False
    assert receipt['inferenceMemoryPeakCalibrated'] is False
    assert run_dir_exists


def test_completed_v11_run_enters_existing_source_candidate_path(monkeypatch):
    import app.main as main

    prompt = '日本語で小さな図書館を設計してください。'
    brief_sha = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
    receipt = {'briefSha256': brief_sha, 'sourceSha256': 'a' * 64,
               'geometrySha256': 'b' * 64}
    report = {'requestSha256': brief_sha, 'sourceSha256': 'a' * 64,
              'geometrySha256': 'b' * 64, 'originalSha256': 'c' * 64,
              'model': MODEL}
    imported = {'design': {'schema': 'design'}, 'scene': {'stats': {'voxels': 8}},
                'report': report, 'reportSha256': 'd' * 64,
                'provenance': 'V11_REPORT_NOT_INDEPENDENTLY_ATTESTED'}
    run_dir = Path('private-v11-run')
    generation_calls = []
    monkeypatch.setattr(main, 'run_v11_studio_generation',
                        lambda **kwargs: (generation_calls.append(kwargs) or
                                          (receipt, run_dir)))

    def read_run(directory, **kwargs):
        assert directory == run_dir
        assert kwargs['expected_prompt'] == prompt
        assert kwargs['expected_model'] == MODEL
        kwargs['compile_design'](imported['design'])
        return imported

    monkeypatch.setattr(main, 'read_v11_run', read_run)
    monkeypatch.setattr(main, 'run_bridge', lambda _engine, design: {'compiled': design})
    studio = object.__new__(main.Studio)
    studio.manager = SimpleNamespace(active_path=Path('pinned-b143'), active_sha='engine-sha')
    studio.history = SimpleNamespace(
        head=lambda: None,
        commit=lambda *_args, **_kwargs: pytest.fail('unapproved SOURCE entered Studio history'))
    studio._confirm_replace_failed_generation = lambda: True
    shown = []

    def hold(design, scene, provider, model, expected_head):
        shown.append((design, scene, provider, model, expected_head))
        studio.pending_revision = {}

    studio._hold_generated_candidate = hold
    studio.caption = SimpleNamespace(set=lambda _value: None)
    studio.status = SimpleNamespace(set=lambda _value: None)
    studio._start = lambda _title, work, done, **_kwargs: done(work())

    studio._generate_v11_studio_new(prompt, MODEL)

    assert shown == [(imported['design'], imported['scene'],
                      'V11_R125_STUDIO', MODEL, None)]
    assert len(generation_calls) == 1
    assert generation_calls[0]['prompt'] == prompt
    assert generation_calls[0]['model'] == MODEL
    assert generation_calls[0]['bounded'] is True
    assert studio.pending_revision['v11Run']['directory'] == str(run_dir)
    assert studio.pending_revision['v11Run']['sourceSha256'] == 'a' * 64


def test_offline_runner_receipt_reaches_real_v11_source_reader(monkeypatch):
    import app.main as main
    from app.v11_source_import import read_external_b143_source

    prompt = '日本語で小さな図書館を設計してください。'
    prompt_raw = prompt.encode('utf-8')
    design = example_library()
    source_raw = json.dumps(design, ensure_ascii=False).encode('utf-8')
    original_raw = json.dumps(
        {'entry': {'shelterDepth': 3}, 'plan': {'levels': []}},
        ensure_ascii=False).encode('utf-8')
    source_sha, geometry_sha = '4' * 64, '5' * 64
    scene = {'sourceSha256': source_sha, 'geometrySha256': geometry_sha,
             'stats': {'voxels': 64}}
    diagnosis = {'status': 'B143_SOURCE_CANDIDATE_ART_HOLD',
                 'sourceVerified': True, **scene}
    report = {
        'schema': 'r125-local-plan-one-shot-v1',
        'status': 'B143_SOURCE_CANDIDATE_ART_HOLD', 'sourceVerified': True,
        'artStatus': 'NOT_REVIEWED', 'constructionApproved': False,
        'originalEdited': False, 'nineWorldGates': ['BLOCKED'] * 9,
        'worldWrites': 0, 'worldContacts': 0, 'automaticRetries': 0,
        'attemptLimit': 1, 'modelCallAttempts': 1, 'modelResponsesCaptured': 1,
        'authorshipMode': 'NEW_MODEL_AUTHORED_R125_RESPONSE',
        'revisionBasePlanSha256': None,
        'requestSha256': hashlib.sha256(prompt_raw).hexdigest(),
        'originalSha256': hashlib.sha256(original_raw).hexdigest(),
        'model': MODEL, **scene,
    }

    with tempfile.TemporaryDirectory(
            prefix='v11-studio-source-import-',
            dir=Path(default_private_module()).parents[1]) as temp:
        run_dir = Path(temp)
        (run_dir / 'REQUEST_ORIGINAL.txt').write_bytes(prompt_raw)
        (run_dir / 'MODEL_RESPONSE_ORIGINAL.txt').write_bytes(original_raw)
        (run_dir / 'B143_SOURCE_REVIEW_ONLY.json').write_bytes(source_raw)
        (run_dir / 'SOURCE_DIAGNOSIS.json').write_text(
            json.dumps(diagnosis), encoding='utf-8')
        (run_dir / 'RUN_REPORT.json').write_text(json.dumps(report), encoding='utf-8')
        receipt = {
            'schema': 'r125-studio-owned-result-v1', 'mode': 'new',
            'status': 'SOURCE_READY_REVIEW_ONLY', 'headSha': V11_HEAD,
            'model': MODEL, 'modelDigest': MODEL_DIGEST,
            'briefSha256': report['requestSha256'],
            'requestSha256': '6' * 64, 'modelPostCount': 1,
            'automaticRetries': 0, 'loadedModelObserved': True,
            'unloadConfirmed': True, 'ownedServiceStopConfirmed': True,
            'monitorStopConfirmed': True, 'projectLockReleased': True,
            'sourceVerified': True, 'sourceSha256': source_sha,
            'geometrySha256': geometry_sha, 'worldContacts': 0, 'worldWrites': 0,
            'constructionApproved': False, 'executionAllowed': False,
            'artStatus': 'NOT_REVIEWED', 'runPath': str(run_dir),
        }
        monkeypatch.setattr(main, 'run_v11_studio_generation',
                            lambda **_kwargs: (receipt, run_dir))
        monkeypatch.setattr(main, 'run_bridge', lambda _engine, _design: scene)
        studio = object.__new__(main.Studio)
        studio.manager = SimpleNamespace(active_path=Path('pinned-b143'), active_sha='engine-sha')
        studio.history = SimpleNamespace(head=lambda: None)
        studio._confirm_replace_failed_generation = lambda: True
        shown = []

        def hold(candidate, compiled, provider, model, expected_head):
            shown.append((candidate, compiled, provider, model, expected_head))
            studio.pending_revision = {}

        studio._hold_generated_candidate = hold
        studio.caption = SimpleNamespace(set=lambda _value: None)
        studio.status = SimpleNamespace(set=lambda _value: None)
        studio._start = lambda _title, work, done, **_kwargs: done(work())
        studio._generate_v11_studio_new(prompt, MODEL)

        assert shown == [(design, scene, 'V11_R125_STUDIO', MODEL, None)]
        assert studio.pending_revision['v11Run']['sourceSha256'] == source_sha
        assert studio.pending_revision['v11Run']['geometrySha256'] == geometry_sha
