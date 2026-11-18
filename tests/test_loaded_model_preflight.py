"""No accidental local model load / incorrect model selection on production paths.

Fake HTTP only. This does not prove actual Windows or local LLM inference.
"""
from __future__ import annotations
import io
import json
import pytest
from app import core
from tests.test_gui_review_flow import studio, _complete
from app import main as studio_main


class LocalAPI:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
    def open(self, req, timeout=None):
        self.calls.append((req.full_url, req.get_method()))
        if not self.replies:
            raise AssertionError('Unexpected network request / possible inference POST')
        payload = self.replies.pop(0)
        return io.BytesIO(json.dumps(payload).encode('utf-8'))


def test_ollama_preflight_only_exact_loaded_model(monkeypatch):
    server = LocalAPI([{'models':[{'name':'qwen3:14b'},{'name':'gemma4:26b'}]}])
    monkeypatch.setattr(core, 'LOCAL_HTTP', server)
    assert core.verify_loaded_model('Ollama', 'qwen3:14b')['loaded']
    assert server.calls == [(core.OLLAMA_BASE + '/api/ps','GET')]
    server = LocalAPI([{'models':[{'name':'gemma4:26b'}]}])
    monkeypatch.setattr(core, 'LOCAL_HTTP', server)
    with pytest.raises(core.StudioError, match='自動ロードせず'):
        core.verify_loaded_model('Ollama', 'qwen3:14b')
    assert server.calls == [(core.OLLAMA_BASE + '/api/ps','GET')]


def test_lm_studio_only_unique_loaded_key_or_instance(monkeypatch):
    server = LocalAPI([{'models':[
        {'key':'stored-model','type':'llm','loaded_instances':[]},
        {'key':'selected-key','type':'llm','loaded_instances':[{'id':'loaded-instance'}]}
    ]}])
    monkeypatch.setattr(core, 'LOCAL_HTTP', server)
    status = core.verify_loaded_model('LM Studio','selected-key')
    assert status['loadedInstance'] == 'loaded-instance'
    assert server.calls == [(core.LM_STUDIO_BASE + '/api/v1/models','GET')]
    for instances in ([], [{'id':'a'}, {'id':'b'}]):
        server = LocalAPI([{'models':[{'key':'selected-key','type':'llm',
                                      'loaded_instances':instances}]}])
        monkeypatch.setattr(core, 'LOCAL_HTTP', server)
        with pytest.raises(core.StudioError, match='切り替えず'):
            core.verify_loaded_model('LM Studio', 'selected-key')
        assert len(server.calls)==1 and server.calls[0][1]=='GET'


def test_preflight_malformed_or_offline_refuses_all_post(monkeypatch):
    for reply in ({}, {'models':'wrong'}):
        server = LocalAPI([reply])
        monkeypatch.setattr(core,'LOCAL_HTTP',server)
        with pytest.raises(core.StudioError, match='確認できません'):
            core.verify_loaded_model('Ollama','model:latest')
        assert all(method=='GET' for _,method in server.calls)
    server = LocalAPI([])
    monkeypatch.setattr(core,'LOCAL_HTTP',server)
    with pytest.raises(core.StudioError, match='不正'):
        core.verify_loaded_model('Ollama', 'model\nwrong')
    assert server.calls == []


def test_gui_generate_blocks_inference_if_model_unloaded(studio,monkeypatch):
    app,_ = studio
    app.prompt.delete('1.0','end')
    app.prompt.insert('1.0','未知の日本語要求から図書館を設計')
    before = app.history.head()['revisionId']
    # Mock only the read-only check; a production inference must never follow.
    def unloaded(*args):
        raise core.StudioError('選択したOllamaモデルが一意にロード済みではありません。Studioは自動ロードせず、推論を開始しません')
    monkeypatch.setattr(studio_main,'verify_loaded_model',unloaded)
    monkeypatch.setattr(studio_main,'ask_llm', lambda *a,**kw:(_ for _ in ()).throw(
        AssertionError('Model inference should not start')))
    monkeypatch.setattr(studio_main.messagebox,'showerror',lambda *a,**k:None)
    app.generate_btn.invoke()
    _complete(app)
    assert app.history.head()['revisionId']==before
    assert app.pending_revision is None


def test_gui_revision_blocks_inference_if_model_unloaded(studio,monkeypatch):
    app,_ = studio
    before = app.history.head()['revisionId']
    monkeypatch.setattr(studio_main,'verify_loaded_model',lambda *a:(_ for _ in ()).throw(
        core.StudioError('モデルがロードされていません')))
    monkeypatch.setattr(studio_main,'ask_llm', lambda *a,**kw:(_ for _ in ()).throw(
        AssertionError('No inference expected')))
    monkeypatch.setattr(studio_main.messagebox,'showerror',lambda *a,**k:None)
    app.revise_btn.invoke()
    _complete(app)
    assert app.history.head()['revisionId']==before
    assert app.pending_revision is None


def test_new_design_source_failure_does_not_trigger_second_inference(studio, monkeypatch):
    app,_ = studio
    app.prompt.delete('1.0', 'end')
    app.prompt.insert('1.0', '未知の日本語の別要求')
    before = app.history.head()['revisionId']
    model_calls = []
    def answer(*args, **kwargs):
        model_calls.append(args)
        return {'schema': core.SCHEMA, 'title':'invalid geometry'}
    monkeypatch.setattr(studio_main, 'ask_llm', answer)
    def fail_compile(engine, design):
        raise core.StudioError('B143_INVALID_CELLS')
    monkeypatch.setattr(studio_main, 'run_bridge', fail_compile)
    monkeypatch.setattr(studio_main.messagebox, 'showerror', lambda *a,**kw:None)
    app.generate_btn.invoke()
    _complete(app)
    assert len(model_calls) == 1
    assert app.history.head()['revisionId'] == before
    assert app.pending_revision is None


def test_revision_source_failure_does_not_trigger_second_inference(studio, monkeypatch):
    app,_ = studio
    original = app.history.head()
    model_calls = []
    def answer(*args, **kwargs):
        model_calls.append(args)
        return {'schema': core.SCHEMA, 'title':'invalid revision geometry'}
    monkeypatch.setattr(studio_main, 'ask_llm', answer)
    old_scene = app.scene
    def fake_bridge(engine, design):
        if design == app.design:
            return old_scene
        raise core.StudioError('B143_INVALID_CELLS')
    monkeypatch.setattr(studio_main, 'run_bridge', fake_bridge)
    monkeypatch.setattr(studio_main.messagebox, 'showerror', lambda *a,**kw:None)
    app.revise_btn.invoke()
    _complete(app)
    assert len(model_calls) == 1
    assert app.history.head()['revisionId'] == original['revisionId']
    assert app.pending_revision is None


def test_acceptance_cli_unloaded_selection_never_attempts_inference(tmp_path,monkeypatch):
    from app.acceptance import run_acceptance
    from tests.test_core import fake_engine
    from tests.test_local_ai_structured import model_server
    engine = fake_engine(tmp_path/'engine')
    with model_server('valid') as (url, posts):
        monkeypatch.setattr(core, 'OLLAMA_BASE', url)
        out = tmp_path/'receipt.json'
        rc = run_acceptance(['--acceptance', '--provider','Ollama',
                             '--model','wrong-model', '--engine',str(engine),
                             '--report',str(out)])
        receipt = json.loads(out.read_text())
        assert rc == 1
        assert receipt['stage'] == 'READ_ONLY_LOADED_MODEL_PREFLIGHT'
        assert not receipt['actualProviderRequestAttempted']
        assert not receipt['studioProject']['saved']
        assert not posts  # A real-model POST was never made.
        assert receipt['worldWrites'] == 0
