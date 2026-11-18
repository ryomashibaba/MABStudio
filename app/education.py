"""MAB B134 diagnostic importer and opt-in, bounded read-only live probe.

The Python layer does not generate arbitrary Minecraft commands. The reviewed
Node bridge uses SocketBE 2.8.0 to send exactly five B134-compatible
``testforblock`` commands only after explicit user interaction in a test world.
Neither a report nor a successful transport claims world-state certification.
"""
from __future__ import annotations
import json
import hashlib
from pathlib import Path
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from app.core import StudioError, atomic_json, node_binary, user_root

B134_SCHEMA = 'minecraftaibuilder-b134-live-readonly-diagnostic-v1'
MAX_DIAGNOSTIC_BYTES = 128*1024
LIVE_BRIDGE_SOURCE_SHA256 = '3603ce3f8554b791a2cd3014bce10cfd057704c0b9ca2bbd80ab51bde5ecafd6'
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
    return validate_b134_report(data)


def validate_b134_report(data: object) -> dict:
    """Validate imported or live B134-compatible data, always unverified."""
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
        if not isinstance(r,dict) or type(r.get('transportError')) is not bool:
            raise StudioError('B134応答が不正です')
        if not isinstance(r.get('statusCode'),(int,type(None))) or isinstance(r.get('statusCode'),bool):
            raise StudioError('B134の状態コードが不正です')
        if (r.get('successCount') not in (0,1,None)
                or isinstance(r.get('successCount'), bool)):
            raise StudioError('B134の成功数が不正です')
        # Never trust a claimed MATCH when the underlying response is unknown.
        status=r.get('statusCode')
        expected=('UNKNOWN' if r['transportError'] or status is None or status<0
                  or r['successCount'] is None else
                  ('MATCH' if r['successCount']==1 else 'NO_MATCH'))
        if row['classification']!=expected:
            raise StudioError('B134の応答と判定に矛盾があります')
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



def parse_probe_coordinates(raw: str) -> tuple[int,int,int]:
    """Match B133/B134 strict integer position constraints."""
    import re
    tokens = raw.strip().split()
    if len(tokens) != 3 or not all(re.fullmatch(r'-?(?:0|[1-9][0-9]*)', t) for t in tokens):
        raise StudioError('読み取り位置は X Y Z の整数3個です')
    x,y,z = (int(t) for t in tokens)
    if abs(x)>30_000_000 or abs(z)>30_000_000 or y< -64 or y>512:
        raise StudioError('読み取り位置がB134の座標範囲外です')
    return x,y,z


def education_bridge_script() -> Path:
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    return root / 'app' / 'education_runtime' / 'bridge.mjs'


def run_education_readonly(
    coordinates: tuple[int,int,int], *, stop: threading.Event | None = None,
    timeout: float = 158.0, script: Path | None = None,
    node: str | None = None, output_root: Path | None = None
) -> dict:
    """Opt-in, bounded read-only B134-style subprocess transport.

    This never opens Minecraft files, executes a shell or sends commands itself.
    Only the pinned Node module may issue the five exact testforblock queries.
    A test fixture may supply a replacement `script`/`node` for offline tests.
    """
    coords = tuple(coordinates)
    if len(coords) != 3 or any(type(v) is not int for v in coords):
        raise StudioError('座標はX Y Zの整数のみ指定してください')
    parse_probe_coordinates(' '.join(map(str,coords)))
    packaged_bridge = script is None
    script = Path(script) if script is not None else education_bridge_script()
    if not script.is_file():
        raise StudioError('Education読取ブリッジが見つかりません。アプリを再配置してください')
    if packaged_bridge and hashlib.sha256(script.read_bytes()).hexdigest()!=LIVE_BRIDGE_SOURCE_SHA256:
        raise StudioError('Education読取プログラムのSHAが一致しません。接続を停止します')
    node = node or node_binary()
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    try:
        process = subprocess.Popen(
            [node,str(script),'--listen',*map(str,coords)],
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,encoding='utf-8',errors='replace',creationflags=flags
        )
    except OSError as exc:
        raise StudioError('教育版の通信プロセスを起動できません: '+str(exc)[:180]) from exc
    try:
        start = time.monotonic()
        while True:
            if stop is not None and stop.is_set():
                raise StudioError('Education接続待機を中止しました。ワールド書込なし')
            remaining=timeout-(time.monotonic()-start)
            if remaining<=0:
                raise StudioError('Education読み取りの待機時間を超えました。Minecraft接続は未確認')
            try:
                stdout,stderr=process.communicate(timeout=min(remaining,0.5))
                break
            except subprocess.TimeoutExpired:
                continue
        if len(stdout)>256*1024 or len(stderr)>16*1024:
            raise StudioError('Education通信の出力が安全な大きさを超えています')
        events=[]
        for line in stdout.splitlines():
            try:
                event=json.loads(line)
            except json.JSONDecodeError as exc:
                raise StudioError('Education通信が正しいJSONを返しませんでした') from exc
            if not isinstance(event,dict) or not isinstance(event.get('type'),str):
                raise StudioError('Education通信のイベント形式が不正です')
            events.append(event)
        outcomes=[e for e in events if e['type'] in ('result','error','timeout')]
        if process.returncode!=0 or len(outcomes)!=1 or outcomes[0]['type']!='result':
            reason=next((e.get('message','') for e in outcomes if e['type'] in ('error','timeout')), '')
            raise StudioError('Education読み取り未完了: '+str(reason or stderr or '接続または照合が成立しませんでした')[:260])
        if len([e for e in events if e['type']=='listening'])!=1:
            raise StudioError('Educationの読み取り待受開始が確認できません')
        raw=outcomes[0].get('report')
        reviewed=validate_b134_report(raw)
        if reviewed['coords'] != dict(zip(('x','y','z'),coords)):
            raise StudioError('Minecraftの読取座標が指定位置と一致しません')
        if outcomes[0].get('worldWrites') != 0:
            raise StudioError('読み取り専用の証拠が一致しません')
        directory=Path(output_root) if output_root is not None else user_root() / 'diagnostics' / 'education'
        filename='B134_STUDIO_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json'
        archive=directory / filename
        # Save only the source report, never a spurious certified-world status.
        atomic_json(archive,raw)
        return {
            'schema':'mab-studio-direct-readonly-v1',
            'coords':reviewed['coords'],
            'outcome':reviewed['outcome'],
            'provisionalStateValue':reviewed['provisionalStateValue'],
            'readbackPath':str(archive),
            'transport':'NODE_PROCESS_REPORTED_5_COMMANDS',
            'authenticatedMinecraftWorld':False,
            'readOnly':True,'worldWrites':0,
            'note':'指定座標の5照合をSocketBE経由で実施したとの子プロセス報告。独立の実機画面・世界ID・状態認証ではありません。'
        }
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
