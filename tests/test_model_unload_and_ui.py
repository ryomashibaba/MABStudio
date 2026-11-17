"""Model unload safety, creation-first GUI and compatibility regressions."""
import io
import json
import os
import pytest
from app import core
from tests.test_gui_review_flow import studio as studio


class Reply:
    def __init__(self, body):
        self.payload = io.BytesIO(json.dumps(body).encode())
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.payload.close()
    def read(self, size=-1):
        return self.payload.read(size)


class MockServer:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
    def open(self, req, timeout=0):
        payload = json.loads(req.data) if getattr(req, 'data', None) else None
        self.requests.append((req.full_url, req.get_method(), payload))
        assert self.responses, 'Unexpected request (possible unintended model operation)'
        return Reply(self.responses.pop(0))


def _lm(*instances):
    return {'models':[{'key':'sample-model','type':'llm','loaded_instances':[
         {'id':id,'config':{'context_length':8192}} for id in instances]}]}


def test_lm_studio_exact_instance_unload_with_readback(monkeypatch):
    server=MockServer([_lm('sample-instance'), {'instance_id':'sample-instance'}, _lm()])
    monkeypatch.setattr(core, 'LOCAL_HTTP', server)
    done=core.unload_local_model('LM Studio','sample-model')
    assert done['unloaded'] is True and done['worldWrites']==0
    assert server.requests == [
        (core.LM_STUDIO_BASE+'/api/v1/models','GET',None),
        (core.LM_STUDIO_BASE+'/api/v1/models/unload','POST',{'instance_id':'sample-instance'}),
        (core.LM_STUDIO_BASE+'/api/v1/models','GET',None),
    ]


def test_lm_studio_refuses_ambiguous_or_missing_instances(monkeypatch):
    for instances in ((), ('instance-a','instance-b')):
        server=MockServer([_lm(*instances)])
        monkeypatch.setattr(core,'LOCAL_HTTP',server)
        with pytest.raises(core.StudioError):
            core.unload_local_model('LM Studio','sample-model')
        assert len(server.requests)==1, 'Unsafe POST must not run'


def test_lm_studio_rejects_mismatched_acknowledgement(monkeypatch):
    server=MockServer([_lm('sample-instance'), {'instance_id':'some-other-instance'}])
    monkeypatch.setattr(core,'LOCAL_HTTP',server)
    with pytest.raises(core.StudioError, match='一致しません'):
        core.unload_local_model('LM Studio','sample-model')
    assert len(server.requests)==2


def test_ollama_unloads_only_running_selected_model(monkeypatch):
    server=MockServer([
        {'models':[{'name':'chosen:latest'},{'name':'keep:latest'}]},
        {'model':'chosen:latest','done':True,'done_reason':'unload'},
        {'models':[{'name':'keep:latest'}]},
    ])
    monkeypatch.setattr(core,'LOCAL_HTTP',server)
    done=core.unload_local_model('Ollama','chosen:latest')
    assert done['unloaded'] is True
    assert server.requests[1]==(
        core.OLLAMA_BASE+'/api/generate','POST',
        {'model':'chosen:latest','keep_alive':0,'stream':False})


def test_ollama_refuses_not_running_without_loading(monkeypatch):
    server=MockServer([{'models':[{'name':'different:latest'}]}])
    monkeypatch.setattr(core,'LOCAL_HTTP',server)
    with pytest.raises(core.StudioError, match='ロードされていません'):
        core.unload_local_model('Ollama','not-loaded:latest')
    assert len(server.requests)==1


def test_ollama_refuses_unverified_unload(monkeypatch):
    server=MockServer([{'models':[{'name':'one:latest'}]},
                       {'model':'one:latest','done':True,'done_reason':'stop'}])
    monkeypatch.setattr(core,'LOCAL_HTTP',server)
    with pytest.raises(core.StudioError):
        core.unload_local_model('Ollama','one:latest')


def test_invalid_model_never_touches_network(monkeypatch):
    class NoRequest:
        def open(self,*args,**kwargs):
            raise AssertionError('Network must not be reached')
    monkeypatch.setattr(core,'LOCAL_HTTP',NoRequest())
    for model in ('','abc\nxxx'):
        with pytest.raises(core.StudioError):
            core.unload_local_model('Ollama',model)
    with pytest.raises(core.StudioError):
        core.unload_local_model('UNKNOWN','any')


def test_creation_first_screen_hides_advanced_controls_and_draft(studio):
    app,_=studio
    app.update()
    assert not app._advanced.winfo_ismapped()
    assert not app.reviewbar.winfo_ismapped()
    assert app.prompt.winfo_ismapped()
    assert app.generate_btn.winfo_ismapped()
    assert app.unload_btn.winfo_ismapped()
    assert app.prompt.winfo_width() < 410  # The 3D area must stay wide.
    assert app.preview.winfo_width() > app.prompt.winfo_width() * 1.7
    assert not app.check_btn.winfo_ismapped()
    app._open_advanced()
    app.update()
    assert app._advanced.winfo_ismapped()
    assert app.check_btn.winfo_ismapped()
    app._advanced.place_forget()
    app.update()
    assert not app._advanced.winfo_ismapped()


def test_draft_review_is_contextual_and_via_approval(studio):
    app,_=studio
    import time
    app.revise_design()
    limit=time.monotonic()+4
    while app.busy and time.monotonic()<limit:
        app.update()
        time.sleep(.02)
    app.update()
    assert app.pending_revision is not None
    assert app.reviewbar.winfo_ismapped()
    app.reject_revision()
    app.update()
    assert not app.reviewbar.winfo_ismapped()


def test_gui_unload_blocked_during_inference(studio,monkeypatch):
    app,_=studio
    called=[]
    monkeypatch.setattr(core,'unload_local_model',lambda *args:called.append(args))
    monkeypatch.setattr('app.main.unload_local_model',lambda *args:called.append(args))
    monkeypatch.setattr('app.main.messagebox.showwarning',lambda *args:None)
    app.busy=True
    app.unload_selected_model()
    assert not called
    app.busy=False


def test_gui_unload_success_preserves_approved_design(studio, monkeypatch):
    import time
    app,_=studio
    app.llm_provider.set('Ollama')
    app.model.set('fixture:latest')
    before=app.history.head()['revisionId']
    calls=[]
    def unload(provider, model):
        calls.append((provider,model))
        return {'provider':provider,'model':model,'unloaded':True,'worldWrites':0}
    monkeypatch.setattr('app.main.unload_local_model',unload)
    app.unload_selected_model()
    end=time.monotonic()+4
    while app.busy and time.monotonic()<end:
        app.update()
        time.sleep(.02)
    app.update()
    assert calls==[('Ollama','fixture:latest')]
    assert app.history.head()['revisionId']==before
    assert app.model.get()=='fixture:latest'  # no automatic reload/inference
    assert 'アンロードしました' in app.status.get()
