"""Single focused offline check of SOURCE -> Education placement and receipts.

No Minecraft client, SocketBE connection, or world modifications are made.
"""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.core import StudioError
from app.education_build import SCHEMA, build_payload, bridge_path, run_education_build

LAYERS = ['B143_STONE','B143_CONCRETE','B143_METAL','B143_GLASS',
          'B143_WOOD','B143_GLOW','B143_ACCENT']


def payload():
    cells=[[x,0,z,0,0] for z in range(2) for x in range(3)]
    cells.append([4,1,0,4,0])
    scene={'sourceSha256':'a'*64,'geometrySha256':'b'*64,
           'stats':{'voxels':len(cells)},'layerNames':LAYERS,'primitives':cells}
    return build_payload(scene,(20,65,-30))


def test_node_plan_and_selftest_are_offline(tmp_path):
    data=payload()
    file=tmp_path/'plan.json'
    file.write_text(json.dumps(data))
    result=subprocess.run(['node',str(bridge_path()),'--plan',str(file)],
                          capture_output=True,text=True,check=True)
    summary=json.loads(result.stdout)
    assert summary=={'type':'plan','commands':2,'blocks':7,
                     'origin':{'x':20,'y':65,'z':-30}}
    result=subprocess.run(['node',str(bridge_path()),'--self-test'],
                          capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)['result']=='PASS'


def fake_node(tmp_path, *, fail=False):
    file=tmp_path/'mock_node.py'
    content='''import json,sys
p=json.load(open(sys.argv[-1],encoding='utf-8'))
def line(typ,**kw):print(json.dumps({'type':typ,**kw}),flush=True)
line('listening')
line('started')
'''
    if fail:
        content+="line('progress',completed=1,commands=2,confirmedBlocks=6)\n"
        content+="line('error',message='COMMAND_REJECTED',completed=1,confirmedBlocks=6)\n"
        content+="sys.exit(2)\n"
    else:
        content+="line('progress',completed=2,commands=2,confirmedBlocks=7)\n"
        content+="line('result',completed=2,commands=2,confirmedBlocks=7,sourceSha256=p['sourceSha256'],geometrySha256=p['geometrySha256'])\n"
    file.write_text(content)
    return file


def test_receipt_success_and_partial_failed_are_not_conflated(tmp_path):
    data=payload()
    success=run_education_build(data,script=fake_node(tmp_path),node=sys.executable,
                                output_root=tmp_path/'receipts')
    assert success['status']=='COMMANDS_ACKNOWLEDGED'
    assert success['confirmedBlocks']==7
    assert not success['readbackVerified']
    assert Path(success['receiptPath']).is_file()
    with pytest.raises(StudioError,match='途中まで配置'):
        run_education_build(data,script=fake_node(tmp_path,fail=True),node=sys.executable,
                            output_root=tmp_path/'partial')
    saved=list((tmp_path/'partial').glob('EDUCATION_BUILD_*.json'))
    assert len(saved)==1
    receipt=json.loads(saved[0].read_text())
    assert receipt['status']=='PARTIAL_OR_FAILED'
    assert receipt['confirmedBlocks']==6


def test_studio_button_uses_persistent_game_session_without_chat(tmp_path):
    """Mock Node side only: once paired, two Studio clicks need no chat token."""
    from app.education_build import EducationBuildSession
    program=tmp_path/'persistent_node.py'
    program.write_text('''import json,sys
print(json.dumps({'type':'listening','port':8001}),flush=True)
for line in sys.stdin:
    req=json.loads(line)
    p=req['payload']
    print(json.dumps({'type':'started','commands':1,'origin':p['origin']}),flush=True)
    print(json.dumps({'type':'result','commands':1,'completed':1,
                      'confirmedBlocks':p['voxels'],
                      'sourceSha256':p['sourceSha256'],
                      'geometrySha256':p['geometrySha256']}),flush=True)
''')
    session=EducationBuildSession(script=program,node=sys.executable)
    project_id='c'*32
    try:
        for i in range(2):
            result=run_education_build(payload(),script=program,node=sys.executable,
                                       session=session,output_root=tmp_path/'logs',
                                       project_id=project_id)
            assert result['status']=='COMMANDS_ACKNOWLEDGED'
            assert result['confirmedBlocks']==7
        assert session.process.poll() is None
        path=tmp_path/'logs'/('EDUCATION_SITE_'+project_id+'.json')
        site=json.loads(path.read_text(encoding='utf-8'))
        assert site['previous']['voxels']==7
        assert site['previous']['origin']=={'x':20,'y':65,'z':-30}
        assert site['uncertain']==[]
    finally:
        session.close()
