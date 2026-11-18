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


def test_lm_studio_rejects_unusable_loaded_instance_id_without_inference(monkeypatch):
    server = LocalAPI([{'models': [{'key': 'saved-key', 'type': 'llm',
                                    'loaded_instances': [{'id': 'bad id\n'}]}]}])
    monkeypatch.setattr(core, 'LOCAL_HTTP', server)
    with pytest.raises(core.StudioError, match='インスタンスIDが不正'):
        core.verify_loaded_model('LM Studio', 'saved-key')
    assert server.calls == [(core.LM_STUDIO_BASE + '/api/v1/models', 'GET')]


@pytest.mark.parametrize('operation', ['generate', 'revise'])
def test_gui_uses_exact_loaded_instance_not_catalog_key(studio, monkeypatch, operation):
    app, _calls = studio
    app.llm_provider.set('LM Studio')
    app.model.set('saved-key')
    model_calls = []
    from tests.test_gui_review_flow import _complete
    from app.examples import example_library
    from tests.test_gui_review_flow import _scene
    import copy

    def preflight(provider, chosen):
        assert (provider, chosen) == ('LM Studio', 'saved-key')
        return {'loaded': True, 'inferenceModel': 'already-loaded-instance',
                'selectedModel': chosen}

    def answer(provider, model, prompt, **kwargs):
        model_calls.append((provider, model, kwargs.get('revision_base')))
        document = copy.deepcopy(example_library())
        document['title'] = 'approved draft candidate'
        return document

    monkeypatch.setattr(studio_main, 'verify_loaded_model', preflight)
    monkeypatch.setattr(studio_main, 'ask_llm', answer)
    if operation == 'generate':
        app.prompt.delete('1.0', 'end')
        app.prompt.insert('1.0', '日本語の未知要求から展望台')
        app.generate_btn.invoke()
    else:
        app.revise_btn.invoke()
    _complete(app)
    assert len(model_calls) == 1
    assert model_calls[0][0:2] == ('LM Studio', 'already-loaded-instance')
    assert (model_calls[0][2] is None) == (operation == 'generate')
    assert app.model.get() == 'saved-key'  # keep the user's picker unchanged
    if operation == 'revise':
        assert app.pending_revision is not None
    else:
        assert app.history.head()['design']['title'] == 'approved draft candidate'


def test_acceptance_uses_exact_loaded_instance_not_catalog_key(tmp_path, monkeypatch):
    from app import acceptance
    from app.examples import example_library
    from tests.test_gui_review_flow import _scene
    import os
    if os.name != 'nt' and not os.environ.get('DISPLAY'):
        pytest.skip('Tk rendering requires DISPLAY')
    engine = tmp_path/'isolated-engine'
    engine.mkdir()
    model_calls = []
    monkeypatch.setattr(acceptance, 'verify_packaged_runtime', lambda: {'mode': 'SOURCE'})
    monkeypatch.setattr(acceptance, 'compatibility_check', lambda _: {'geometrySha256': 'fake-smoke'})
    monkeypatch.setattr(acceptance, 'verify_loaded_model', lambda provider, model: {
        'provider': provider, 'selectedModel': model, 'inferenceModel': 'already-loaded-instance',
        'loaded': True, 'verifiedBy': 'READ_ONLY_API_V1_MODELS'})
    monkeypatch.setattr(acceptance, 'ask_llm', lambda provider, model, prompt, **kw:
                        (model_calls.append((provider, model)), example_library())[1])
    monkeypatch.setattr(acceptance, 'run_bridge', lambda e, d: _scene(d))
    report = tmp_path/'receipt.json'
    rc = acceptance.run_acceptance(['--acceptance', '--provider','LM Studio', '--model',
                                     'selected-catalog-key', '--engine', str(engine),
                                     '--report', str(report)])
    receipt = json.loads(report.read_text(encoding='utf-8'))
    assert rc == 0 and receipt['outcome'] == 'PASS_SOURCE_AND_GUI_ONLY'
    assert model_calls == [('LM Studio', 'already-loaded-instance')]
    assert receipt['model'] == 'selected-catalog-key'
    assert receipt['inferenceModel'] == 'already-loaded-instance'
    assert receipt['worldWrites'] == 0
