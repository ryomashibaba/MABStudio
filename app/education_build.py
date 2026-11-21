"""Place frozen B143-verified SOURCE voxels into the connected Minecraft Education world.

No second design engine: the payload is prepared only from an actual run_bridge()
scene. This module never touches .mcworld files and never loads an LLM. The game
connection is established once by the player with /wsserver.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from queue import Queue, Empty
import subprocess
import sys
import tempfile
import threading
import time

from app.core import StudioError, atomic_json, node_binary, user_root
from app.education import parse_probe_coordinates

SCHEMA = 'mab-studio-education-placement-v1'
BUILD_BRIDGE_SHA256 = '5e72afc0218a02cd6f21ce9439348de8e25a052447cf4fdee8e1a4458d06f7f9'
MAX_OUTPUT_LINE = 16_384


def bridge_path() -> Path:
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    return root / 'app' / 'education_runtime' / 'build.mjs'


def build_payload(scene: dict, xyz: tuple[int, int, int], *, use_player_position: bool = False) -> dict:
    """Transport only the exact cells/materials of a recently verified B143 scene."""
    x, y, z = parse_probe_coordinates(' '.join(map(str, xyz)))
    if not isinstance(scene, dict) or not isinstance(scene.get('primitives'), list):
        raise StudioError('検証済みB143の3Dボクセルがありません')
    return {
        'schema': SCHEMA,
        'placementMode': 'player' if use_player_position else 'manual',
        'sourceSha256': scene['sourceSha256'],
        'geometrySha256': scene['geometrySha256'],
        'origin': {'x': x, 'y': y, 'z': z},
        'layerNames': scene['layerNames'],
        'voxels': scene['stats']['voxels'],
        'primitives': scene['primitives'],
    }


class EducationBuildSession:
    """Keep the local SocketBE listener alive between GUI button presses.

    Game pairing remains one /wsserver command per game connection; a build
    itself needs only one Studio button click. No Minecraft commands on startup.
    """
    def __init__(self, *, script: Path | None = None, node: str | None = None):
        self.script = Path(script) if script is not None else bridge_path()
        self.node = node
        self.process = None
        self.output = Queue()
        self._lock = threading.Lock()

    def ensure_started(self):
        with self._lock:
            if self.process is not None:
                if self.process.poll() is None:
                    return self.process
                raise StudioError('Education接続が終了しました。Studioを起動し直してください')
            if not self.script.is_file():
                raise StudioError('Education施工ブリッジがありません')
            if self.script == bridge_path() and hashlib.sha256(self.script.read_bytes()).hexdigest() != BUILD_BRIDGE_SHA256:
                raise StudioError('Education施工ブリッジの整合性が一致しません')
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            self.process = subprocess.Popen(
                [self.node or node_binary(), str(self.script), '--serve'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='replace', bufsize=1,
                creationflags=flags)
            process, output = self.process, self.output
            def read_stdout():
                try:
                    for line in process.stdout:
                        output.put(('event',line))
                finally:
                    output.put(('closed',None))
            def read_stderr():
                try:
                    for _ in process.stderr: pass
                except OSError: pass
            threading.Thread(target=read_stdout,daemon=True).start()
            threading.Thread(target=read_stderr,daemon=True).start()
            return self.process

    def submit(self,payload, *, project_id: str | None = None):
        proc=self.ensure_started()
        if proc.poll() is not None:raise StudioError('Education接続プロセスが停止しています')
        try:
            request={'action':'build','payload':payload}
            if project_id is not None:
                if not isinstance(project_id,str) or len(project_id)!=32 or any(
                        ch not in '0123456789abcdef' for ch in project_id):
                    raise StudioError('プロジェクト識別子が不正です')
                request['projectId']=project_id
            proc.stdin.write(json.dumps(request,separators=(',',':'))+'\n')
            proc.stdin.flush()
        except (BrokenPipeError,OSError) as e:
            raise StudioError('Education接続へ建築を依頼できません') from e

    def abort(self):
        """Stop the bridge itself, not merely the GUI progress listener."""
        with self._lock:
            proc, self.process = self.process, None
            self.output = Queue()
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)

    def close(self):
        self.abort()


def run_education_build(payload: dict, *, stop: threading.Event | None = None,
                        on_progress=None, node: str | None = None,
                        script: Path | None = None, output_root: Path | None = None,
                        timeout: float = 3600.0, session: EducationBuildSession | None = None,
                        project_id: str | None = None) -> dict:
    """Send validated blocks; use a persistent paired world if session is provided."""
    script = Path(script) if script is not None else bridge_path()
    if not script.is_file():
        raise StudioError('Education施工ブリッジが見つかりません。新しいStudioをビルドしてください')
    if session is None and script == bridge_path() and hashlib.sha256(script.read_bytes()).hexdigest() != BUILD_BRIDGE_SHA256:
        raise StudioError('Education施工ブリッジがソースと一致しません')
    node = node or node_binary()
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    destination = Path(output_root) if output_root is not None else user_root() / 'diagnostics' / 'education-build'
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    receipt = destination / f'EDUCATION_BUILD_{stamp}.json'
    record = {
        'schema': 'mab-studio-education-build-receipt-v1',
        'createdAt': datetime.now(timezone.utc).isoformat(),
        'sourceSha256': payload.get('sourceSha256'),
        'geometrySha256': payload.get('geometrySha256'),
        'origin': payload.get('origin'),
        'status': 'STARTING', 'confirmedBlocks': 0, 'completedCommands': 0,
        'targetCells': payload.get('voxels'),
        'readbackVerified': False, 'gameConnectionConfirmed': False,
    }
    output = Queue()
    events = []
    stderr_parts = []
    proc = None
    with tempfile.TemporaryDirectory(prefix='mab-education-build-') as temp:
        file = Path(temp) / 'payload.json'
        file.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        try:
            if session is not None:
                proc=session.ensure_started()
                output=session.output
                session.submit(payload,project_id=project_id)
            else:
                proc = subprocess.Popen(
                    [node, str(script), '--listen', str(file)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding='utf-8', errors='replace', creationflags=flags)
            def stdout_reader():
                try:
                    for line in proc.stdout:
                        output.put(('event', line))
                finally:
                    output.put(('closed', None))
            def stderr_reader():
                try:
                    for line in proc.stderr:
                        if len(stderr_parts) < 20:
                            stderr_parts.append(line[:250])
                except OSError:
                    pass
            if session is None:
                threading.Thread(target=stdout_reader, daemon=True).start()
                threading.Thread(target=stderr_reader, daemon=True).start()
            started = time.monotonic()
            stdout_closed = False
            while True:
                if stop is not None and stop.is_set():
                    if session is not None:
                        session.abort()
                    raise StudioError('施工送信を停止しました。実行済みのブロックは残る可能性があります')
                if time.monotonic() - started > timeout:
                    raise StudioError('Education施工が時間切れです。途中まで配置された可能性があります')
                try:
                    kind, line = output.get(timeout=0.2)
                except Empty:
                    if proc.poll() is not None and stdout_closed:
                        break
                    continue
                if kind == 'closed':
                    stdout_closed = True
                    if proc.poll() is not None:
                        break
                    continue
                if len(line) > MAX_OUTPUT_LINE:
                    raise StudioError('Education施工の出力が不正に長すぎます')
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise StudioError('Education施工の応答形式が不正です') from exc
                if not isinstance(event, dict) or event.get('type') not in (
                        'listening', 'started', 'progress', 'result', 'error', 'timeout'):
                    raise StudioError('Education施工のイベントが不正です')
                events.append(event)
                if event['type'] == 'listening' and on_progress:
                    on_progress('Education接続待機中。初回のみゲーム内で /wsserver 127.0.0.1:8001')
                if event['type'] == 'started':
                    record['origin'] = event.get('origin', record['origin'])
                    record['gameConnectionConfirmed'] = True
                    record['status'] = 'BUILDING'
                if event['type'] in ('progress', 'result', 'error'):
                    record['completedCommands'] = event.get('completed', record['completedCommands'])
                    record['confirmedBlocks'] = event.get('confirmedBlocks', record['confirmedBlocks'])
                    if event['type'] == 'progress' and on_progress:
                        on_progress(f'Educationに配置中: {record["completedCommands"]}/{event["commands"]}命令、'
                                    f'{record["confirmedBlocks"]:,}セル')
                if event['type'] in ('error', 'timeout'):
                    record['status'] = 'PARTIAL_OR_FAILED'
                    record['error'] = str(event.get('message', 'Education通信失敗'))[:300]
                if event['type'] == 'result':
                    record['status'] = 'COMMANDS_ACKNOWLEDGED'
                    record['totalCommands'] = event.get('commands')
                    record['plannedBlocks'] = event.get('plannedBlocks',payload['voxels'])
                    record['targetCells'] = event.get('targetCells',payload['voxels'])
                    record['changeCounts'] = event.get('changes')
                    record['readbackVerified'] = False
                    if event.get('sourceSha256') != payload.get('sourceSha256') or (
                            event.get('geometrySha256') != payload.get('geometrySha256')):
                        raise StudioError('Education施工応答のSOURCEが一致しません')
                if session is not None and event['type'] in ('result','error','timeout'):
                    break
                if proc.poll() is not None and stdout_closed:
                    break
            if session is None:
                proc.wait(timeout=2)
            results = [e for e in events if e['type'] == 'result']
            if ((session is None and proc.returncode != 0) or len(results) != 1 or
                    record['confirmedBlocks'] != record.get('plannedBlocks',payload['voxels'])):
                reason = record.get('error') or ''.join(stderr_parts)[-240:] or 'コマンドの全件確認が取れませんでした'
                raise StudioError('Education施工未完了: ' + reason +
                                  f'（確認済み {record["confirmedBlocks"]}セル。途中まで配置済みの可能性）')
            record['status'] = 'COMMANDS_ACKNOWLEDGED'
            return {**record, 'receiptPath': str(receipt)}
        except Exception as exc:
            record['status'] = 'PARTIAL_OR_FAILED'
            record['error'] = str(exc)[:400]
            raise
        finally:
            if session is None and proc is not None and proc.poll() is None:
                proc.terminate()
                try: proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait(timeout=2)
            atomic_json(receipt, record)
