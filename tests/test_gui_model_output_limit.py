"""GUI should stop a costly local LLM request on length, without misblaming MAB."""
from tests.test_gui_review_flow import studio as studio  # shared real Tk fixture
from app import main as studio_main, core
import time


def _wait(app):
    end=time.monotonic()+3
    while app.busy and time.monotonic()<end:
        app.update()
        time.sleep(.02)
    app.update()
    assert not app.busy


def test_gui_generation_length_does_not_infer_twice_or_overwrite(studio,monkeypatch):
    app,_=studio
    messages=[]
    calls=[]
    monkeypatch.setattr(studio_main.messagebox,'showerror',lambda *args:messages.append(args))
    def cut(*args,**kwargs):
        calls.append(1)
        raise core.ModelOutputLimitError('LM Studioの設計JSON生成がトークン上限で途中終了しました')
    monkeypatch.setattr(studio_main,'ask_llm',cut)
    original=app.history.head()['revisionId']
    app.generate()
    _wait(app)
    assert len(calls)==1
    assert len(messages)==1
    assert 'トークン上限' in messages[0][1]
    assert 'MAB SOURCEが拒否' not in messages[0][1]
    assert app.history.head()['revisionId']==original


def test_gui_revision_length_keeps_approved_source(studio,monkeypatch):
    app,_=studio
    messages=[]
    calls=[]
    monkeypatch.setattr(studio_main.messagebox,'showerror',lambda *args:messages.append(args))
    def cut(*args,**kwargs):
        calls.append(1)
        raise core.ModelOutputLimitError('LM Studioの設計JSON生成がトークン上限で途中終了しました')
    monkeypatch.setattr(studio_main,'ask_llm',cut)
    original=app.history.head()['revisionId']
    app.revise_design()
    _wait(app)
    assert len(calls)==1
    assert len(messages)==1
    assert 'トークン上限' in messages[0][1]
    assert app.history.head()['revisionId']==original
    assert app.pending_revision is None
