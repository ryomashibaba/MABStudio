"""Studio approval must not grant formal ART or Minecraft write authority."""
from __future__ import annotations

import json
import subprocess

import pytest

from app.core import StudioError
from app.core import node_binary
from app.education_build import (FORMAL_ART_ADOPTION_VERIFIED,
                                 EducationBuildSession, bridge_path,
                                 run_education_build)


def _payload():
    return {'schema': 'mab-studio-education-placement-v1',
            'placementMode': 'manual', 'sourceSha256': 'a' * 64,
            'geometrySha256': 'b' * 64,
            'origin': {'x': 0, 'y': 64, 'z': 0},
            'layerNames': ['B143_STONE', 'B143_CONCRETE', 'B143_METAL',
                           'B143_GLASS', 'B143_WOOD', 'B143_GLOW', 'B143_ACCENT'],
            'voxels': 1, 'primitives': [[0, 0, 0, 0, 0]]}


def test_python_studio_execution_paths_hold_before_starting_node(tmp_path):
    assert FORMAL_ART_ADOPTION_VERIFIED is False
    output = tmp_path / 'receipts'
    with pytest.raises(StudioError, match='正式ART'):
        run_education_build(_payload(), output_root=output)
    assert not output.exists()

    session = EducationBuildSession()
    with pytest.raises(StudioError, match='正式ART'):
        session.ensure_started()
    assert session.process is None


def test_node_network_modes_are_blocked_without_opening_a_listener(tmp_path):
    script = bridge_path()
    node = node_binary()
    payload_file = tmp_path / 'payload.json'
    payload_file.write_text(json.dumps(_payload()), encoding='utf-8')
    for args in (['--serve'], ['--listen', str(payload_file)]):
        result = subprocess.run([node, str(script), *args], capture_output=True,
                                text=True, timeout=8)
        assert result.returncode != 0
        report = json.loads(result.stdout.strip().splitlines()[-1])
        assert report['type'] == 'error'
        assert 'FORMAL_ART_ADOPTION_REQUIRED' in report['message']


def test_imported_world_write_primitive_is_blocked_even_with_fake_world(tmp_path):
    script = tmp_path / 'verify_perform.mjs'
    bridge_uri = bridge_path().as_uri()
    script.write_text(
        f"import {{ perform }} from {json.dumps(bridge_uri)};\n"
        "let calls=0;\n"
        "try { await perform({runCommand:async()=>{calls++;return {statusCode:0};}},"
        "{commands:[]}); process.exit(2); }\n"
        "catch(e) { if(!String(e.message).includes('FORMAL_ART_ADOPTION_REQUIRED'))process.exit(3); }\n"
        "if(calls!==0)process.exit(4);\n", encoding='utf-8')
    result = subprocess.run([node_binary(), str(script)], capture_output=True,
                            text=True, timeout=8)
    assert result.returncode == 0, result.stderr
