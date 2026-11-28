import hashlib
import json

from app.v11_watch import newest_v11_ready_run


def test_completed_one_shot_run_is_discovered_through_trusted_feed(tmp_path):
    root = tmp_path / 'studio'
    root.mkdir()
    feed = tmp_path / 'trusted'
    runs = feed / 'runs' / 'run-1'
    runs.mkdir(parents=True)
    config = root / 'auto-update'
    config.mkdir()
    (config / 'trusted-feed.txt').write_text(str(feed), encoding='utf-8')
    report = {
        'schema': 'r125-local-plan-one-shot-v1',
        'status': 'B143_SOURCE_CANDIDATE_ART_HOLD', 'sourceVerified': True,
        'constructionApproved': False, 'worldWrites': 0, 'worldContacts': 0,
        'attemptLimit': 1, 'modelCallAttempts': 1, 'model': 'qwen3:14b',
    }
    raw = json.dumps(report).encode('utf-8')
    (runs / 'RUN_REPORT.json').write_bytes(raw)
    for name in ('REQUEST_ORIGINAL.txt', 'MODEL_RESPONSE_ORIGINAL.txt',
                 'SOURCE_DIAGNOSIS.json', 'B143_SOURCE_REVIEW_ONLY.json'):
        (runs / name).write_bytes(b'private run payload')
    ready = newest_v11_ready_run(root)
    assert ready == (runs, hashlib.sha256(raw).hexdigest(), 'R125 qwen3:14b')
    assert newest_v11_ready_run(root, ready[1]) is None
    assert (runs / 'MODEL_RESPONSE_ORIGINAL.txt').read_bytes() == b'private run payload'


def test_incomplete_or_unsafe_report_is_not_shown_as_ready(tmp_path):
    root = tmp_path / 'studio'
    root.mkdir()
    inbox = root / 'v11-inbox' / 'runs' / 'bad'
    inbox.mkdir(parents=True)
    (inbox / 'RUN_REPORT.json').write_text(json.dumps({
        'schema': 'r125-local-plan-one-shot-v1',
        'status': 'HOLD_MODEL_OR_SOURCE', 'modelCallAttempts': 1,
    }), encoding='utf-8')
    assert newest_v11_ready_run(root) is None
