"""Local GUI model choices only; never stores prompts, credentials, or model output."""
from __future__ import annotations
import json
from pathlib import Path
from app.core import StudioError, atomic_json

SCHEMA = 'mab-studio-local-interface-preferences-v1'
PROVIDERS = ('Ollama','LM Studio')


def read_local_choice(root: Path) -> tuple[str, str]:
    """Recover a previously chosen provider/model, never loading or running it."""
    path=Path(root)/'preferences.json'
    if not path.exists():
        return 'Ollama',''
    try:
        d=json.loads(path.read_text(encoding='utf-8'))
    except (ValueError,UnicodeError,OSError):
        return 'Ollama',''
    if not isinstance(d,dict) or d.get('schema') != SCHEMA:
        return 'Ollama',''
    provider,model=d.get('provider'),d.get('model')
    if provider not in PROVIDERS or not isinstance(model,str) or len(model)>180 or any(ord(c)<32 for c in model):
        return 'Ollama',''
    return provider,model


def save_local_choice(root: Path,provider: str,model: str) -> None:
    if provider not in PROVIDERS or not isinstance(model,str) or len(model)>180 or any(ord(c)<32 for c in model):
        raise StudioError('ローカルAIの選択内容が不正です')
    atomic_json(Path(root)/'preferences.json',{
        'schema':SCHEMA, 'provider':provider, 'model':model,
        'doesNotStartInference': True, 'worldWrites':0
    })
