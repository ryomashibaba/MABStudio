"""Offline proofs only: not Minecraft Education live tests."""
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from app.core import StudioError
from app.education import LIVE_BRIDGE_SOURCE_SHA256
import hashlib
from app.education import (
    education_bridge_script, parse_probe_coordinates, run_education_readonly,
    validate_b134_report,
)

NODE=shutil.which('node')


def test_packaged_transport_source_pin():
    assert hashlib.sha256(education_bridge_script().read_bytes()).hexdigest() == LIVE_BRIDGE_SOURCE_SHA256


def test_xyz_strict_and_world_height():
    assert parse_probe_coordinates('123 -60 -18') == (123,-60,-18)
    for value in ('0.1 64 1', '0 100000 0', '0 -65 0', '0 513 0',
                  '0 64 0;setblock', '1e2 64 1', '00 64 0'):
        with pytest.raises(StudioError):
            parse_probe_coordinates(value)


@pytest.mark.skipif(not NODE,reason='Node required')
def test_bridge_offline_self_test_allowlist():
    result=subprocess.run([NODE,str(education_bridge_script()),'--self-test'],
                          capture_output=True,text=True,timeout=6)
    assert result.returncode == 0, result.stderr
    data=json.loads(result.stdout)
    assert data == {'type':'test','result':'PASS','commands':5,'worldWrites':0}


def fake_socketbe(source: Path, tmp_path: Path, *, tamper=False) -> Path:
    folder=tmp_path/'runtime'
    folder.mkdir()
    script=folder/'bridge.mjs'
    script.write_bytes(source.read_bytes())
    module=folder/'node_modules'/'socket-be'
    module.mkdir(parents=True)
    (module/'package.json').write_text(json.dumps({
        'name':'socket-be','version':'0.0.0-TEST-STUB','type':'module',
        'exports':'./index.mjs'}),encoding='utf-8')
    # This stub is test-only and never copied to the release ZIP.
    (module/'index.mjs').write_text('''
import {EventEmitter} from 'node:events';
export const ServerEvent={Open:'open',PlayerChat:'chat'};
export class Server extends EventEmitter {
  constructor(options){
    super();
    if(options.port!==8000 || options.webSocketOptions.host!=='127.0.0.1')
      throw Error('UNSAFE_LISTENER');
    queueMicrotask(()=>{
      this.emit('open');
      this.emit('chat',{message:'!mab-read',world:{
        runCommand:async (cmd,opts)=>{
          if(!cmd.startsWith('testforblock '))throw Error('WRITES_NOT_ALLOWED');
          if(opts.timeout!==8000 || opts.noResponse!==false)throw Error('BAD_OPTIONS');
          const ok=cmd.includes('weirdo_direction') ? cmd.endsWith('= 2]') : true;
          return {statusCode:0,successCount:ok?1:0};
        }
      }});
    });
  }
}
''',encoding='utf-8')
    if tamper:
        text=script.read_text(encoding='utf-8')
        script.write_text(text.replace("good?hits[0]:null", "good?0:null"),encoding='utf-8')
    return script


@pytest.mark.skipif(not NODE,reason='Node required')
def test_full_python_node_transport_with_test_double(tmp_path):
    script=fake_socketbe(education_bridge_script(),tmp_path)
    result=run_education_readonly((123,-60,-18),script=script,node=NODE,
                                  output_root=tmp_path/'reports',timeout=5)
    assert result['provisionalStateValue']==2
    assert result['outcome']=='UNIQUE_FOUR_STATE_RESPONSE_NEEDS_VISUAL_REVIEW'
    assert result['worldWrites']==0
    assert result['authenticatedMinecraftWorld'] is False
    file=Path(result['readbackPath'])
    assert file.is_file()
    report=json.loads(file.read_text(encoding='utf-8'))
    assert len(report['results'])==5
    assert all(r['command'].startswith('testforblock ') for r in report['results'])
    assert validate_b134_report(report)['provisionalStateValue']==2


@pytest.mark.skipif(not NODE,reason='Node required')
def test_tampered_direction_is_not_saved(tmp_path):
    script=fake_socketbe(education_bridge_script(),tmp_path,tamper=True)
    with pytest.raises(StudioError,match='矛盾'):
        run_education_readonly((123,-60,-18),script=script,node=NODE,
                               output_root=tmp_path/'reports',timeout=5)
    assert not (tmp_path/'reports').exists()


@pytest.mark.skipif(not NODE,reason='Node required')
def test_cancel_terminates_node_and_leaves_no_report(tmp_path):
    script=tmp_path/'blocked.mjs'
    script.write_text('setInterval(()=>{},1000);',encoding='utf-8')
    stop=threading.Event()
    timer=threading.Timer(0.2,stop.set)
    timer.start()
    try:
        with pytest.raises(StudioError,match='中止'):
            run_education_readonly((1,64,2),script=script,node=NODE,
                                   timeout=5,stop=stop,output_root=tmp_path/'reports')
    finally:
        timer.cancel()
    assert not (tmp_path/'reports').exists()


@pytest.mark.skipif(not NODE,reason='Node required')
def test_missing_socketbe_fails_closed_without_report(tmp_path):
    # True library is optional and no npm install is done in this test.
    script=tmp_path/'bridge.mjs'
    script.write_bytes(education_bridge_script().read_bytes())
    with pytest.raises(StudioError,match='SOCKET_BE_MISSING'):
        run_education_readonly((1,64,2),script=script,node=NODE,
                               timeout=5,output_root=tmp_path/'reports')
    assert not (tmp_path/'reports').exists()
