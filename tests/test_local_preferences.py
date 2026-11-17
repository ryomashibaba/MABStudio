"""Regular app use: remember local AI selection, no model activation."""
import json
from app.preferences import SCHEMA, read_local_choice, save_local_choice
from app.core import StudioError
from tests.test_gui_review_flow import studio
import pytest


def test_local_model_choice_reopens_without_prompt_or_secret(tmp_path):
    save_local_choice(tmp_path, 'LM Studio', 'qwen-local:27b')
    assert read_local_choice(tmp_path)==('LM Studio','qwen-local:27b')
    data=json.loads((tmp_path/'preferences.json').read_text(encoding='utf-8'))
    assert data=={'schema':SCHEMA,'provider':'LM Studio','model':'qwen-local:27b',
                  'doesNotStartInference':True,'worldWrites':0}
    with pytest.raises(StudioError):
        save_local_choice(tmp_path,'https://outside.net','wrong-model')
    assert read_local_choice(tmp_path)==('LM Studio','qwen-local:27b')


def test_corrupt_preferences_are_not_overwritten_on_read(tmp_path):
    path=tmp_path/'preferences.json'
    path.write_text('{unreadable',encoding='utf-8')
    assert read_local_choice(tmp_path)==('Ollama','')
    assert path.read_text(encoding='utf-8')=='{unreadable'
    path.write_text(json.dumps({'schema':SCHEMA,'provider':'LM Studio', 'model':'bad\nsecret'}))
    assert read_local_choice(tmp_path)==('Ollama','')


def test_gui_remembers_model_without_modifying_approved_project(studio):
    app,_=studio
    active=app.history.head()['revisionId']
    latest=(app.manager.root/'outputs'/'latest-design.json').read_bytes()
    app.llm_provider.set('LM Studio')
    app.model.set('local-special:latest')
    app._remember_model_choice()
    assert read_local_choice(app.manager.root)==('LM Studio','local-special:latest')
    assert app.history.head()['revisionId']==active
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes()==latest
    assert app.history.head()['worldWrites']==0
