"""Read-only intake of existing MAB B134 Education WebSocket diagnostics.

This module deliberately does not create a WebSocket server, send commands, or
claim a supplied JSON file proves live Minecraft access. It interoperates with
MAB's existing B134 diagnostic schema without duplicating its world runtime.
"""
from __future__ import annotations
import json
from pathlib import Path
from app.core import StudioError

B134_SCHEMA = 'minecraftaibuilder-b134-live-readonly-diagnostic-v1'
MAX_DIAGNOSTIC_BYTES = 128*1024
ALLOWED_OUTCOMES = {'UNIQUE_FOUR_STATE_RESPONSE_NEEDS_VISUAL_REVIEW',
                    'INCONCLUSIVE_NEEDS_GAME_INSPECTION'}
ALLOWED_CLASSIFICATIONS = {'MATCH','NO_MATCH','UNKNOWN'}


def import_b134_readback(path: str | Path) -> dict:
    """Parse bounded untrusted B134 read-only JSON; display-only, NOT world state."""
    filepath=Path(path)
    if not filepath.is_file() or filepath.stat().st_size>MAX_DIAGNOSTIC_BYTES:
        raise StudioError('B134診断JSONがありません、または大きすぎます')
    try:
        data=json.loads(filepath.read_text(encoding='utf-8'))
    except (UnicodeError,json.JSONDecodeError) as exc:
        raise StudioError('B134診断JSONを正しく読み取れません') from exc
    if not isinstance(data,dict) or data.get('schema')!=B134_SCHEMA:
        raise StudioError('MAB B134の読み取り専用診断形式ではありません')
    if (data.get('readiness')!='NOT_PREPARED'
            or data.get('constructionAllowed') is not False
            or data.get('authenticatedStairCertification') is not False
            or data.get('operations') != []
            or data.get('independentImageReview') is not False
            or data.get('walkabilityVerified') is not False):
        raise StudioError('B134診断の読み取り専用・未認証条件が一致しません')
    coords=data.get('coords')
    if not isinstance(coords,dict) or set(coords) != {'x','y','z'} or not all(
            type(v) is int and abs(v)<=30_000_000 for v in coords.values()):
        raise StudioError('B134座標の形式が不正です')
    if not -64<=coords['y']<=512:
        raise StudioError('B134のワールド高さが安全範囲外です')
    checks=data.get('results')
    if not isinstance(checks,list) or len(checks)!=5:
        raise StudioError('B134の5件の照合記録がありません')
    expected_prefix='testforblock '+ ' '.join(str(coords[axis]) for axis in ('x','y','z')) + ' '
    expected_commands=[expected_prefix+'minecraft:oak_stairs']
    expected_commands += [expected_prefix+f'minecraft:oak_stairs ["weirdo_direction" = {n}]'
                          for n in range(4)]
    # A parser must never pass the imported string to runCommand or a shell.
    for i,(row,command) in enumerate(zip(checks,expected_commands)):
        if (not isinstance(row,dict) or row.get('command')!=command
                or row.get('classification') not in ALLOWED_CLASSIFICATIONS):
            raise StudioError(f'B134コマンド/結果が第{i+1}件で一致しません')
        r=row.get('response')
        if not isinstance(r,dict) or r.get('transportError') not in (True,False):
            raise StudioError('B134応答が不正です')
        if not isinstance(r.get('statusCode'),(int,type(None))) or isinstance(r.get('statusCode'),bool):
            raise StudioError('B134の状態コードが不正です')
        if r.get('successCount') not in (0,1,None):
            raise StudioError('B134の成功数が不正です')
    if data.get('outcome') not in ALLOWED_OUTCOMES:
        raise StudioError('B134診断の判定種別が不正です')
    candidate=data.get('provisionalStateValue')
    if candidate is not None and (type(candidate) is not int or not 0<=candidate<=3):
        raise StudioError('B134の方向値候補が不正です')
    if (data['outcome']=='INCONCLUSIVE_NEEDS_GAME_INSPECTION') != (candidate is None):
        raise StudioError('B134の不確定判定と方向値候補が矛盾します')
    if data['outcome']=='UNIQUE_FOUR_STATE_RESPONSE_NEEDS_VISUAL_REVIEW':
        classifications=[x['classification'] for x in checks]
        if classifications[0]!='MATCH' or classifications[1:].count('MATCH')!=1 or classifications[1:].count('NO_MATCH')!=3 or classifications[1+candidate]!='MATCH':
            raise StudioError('B134の方向値と5件の照合が矛盾します')
    return {
        'schema':'mab-studio-b134-import-v1',
        'source':B134_SCHEMA,'coords':coords,
        'outcome':data['outcome'], 'provisionalStateValue':candidate,
        'readOnly':True,'externalEvidenceUnverified':True,
        'liveEducationConnectionFromStudio':'NOT_TESTED',
        'independentImageReview':False, 'walkabilityVerified':False,
        'worldContacts':0,'worldWrites':0,
        'note':'外部B134診断のデータ形式を照合しただけです。真正ゲームログとは独立認証していません。'
    }
