"""GPU viewer: full-face output is offline, inert, and packaged for Windows."""
import json
from pathlib import Path
import os
import re
import shutil
import subprocess

import pytest

from app.gpu_viewer import GPUViewerError, compact_display_data, render_offline_html, create_offline_preview
from tests.test_gui_review_flow import studio


def scene():
    return {
        'previewLabel': '1階 <script>alert(1)</script> 確認',
        'primitives': [[0,0,0,0,0],[0,1,0,3,0],[1,0,0,2,0]],
        'previewPalette': ['#BABBCC','#AACCDD','#718391','#6dcce2'],
        'sourceSha256': '1'*64,
        'worldWriteCredential': 'MUST_NEVER_BE_LEAKED',
    }


def test_json_contains_only_inert_geometry_and_no_sensitive_scene_fields():
    before=json.dumps(scene(),ensure_ascii=False)
    output=compact_display_data(scene(),cut_level=0)
    assert output['voxels']==[[0,0,0,0],[0,1,0,3],[1,0,0,2]]
    assert output['sectionLevel']==0
    assert output['bounds']['maxY']==1
    assert 'worldWriteCredential' not in output
    assert 'sourceSha256' not in output
    assert json.dumps(scene(),ensure_ascii=False)==before


def test_inline_html_requires_no_cdn_or_http_or_external_dependencies(tmp_path):
    body=render_offline_html(scene(),cut_level=0)
    assert 'WebGL' in body and 'canvas' in body
    assert '</script> 確認' not in body
    assert 'MUST_NEVER_BE_LEAKED' not in body
    assert '__MAB_SCENE_JSON__' not in body
    assert '__MAB_VIEWER_JS__' not in body
    assert not re.search(r'<script\s+src=',body)
    assert 'https://' not in body and 'http://' not in body
    path=create_offline_preview(scene(),tmp_path,cut_level=0)
    assert path.read_text(encoding='utf-8')==body
    assert path.parent==tmp_path/'gpu-preview'
    assert path.stat().st_size>9000
    if os.name != 'nt':
        assert path.stat().st_mode & 0o077 == 0
    again=create_offline_preview(scene(),tmp_path)
    assert again!=path and path.read_text(encoding='utf-8')==body


@pytest.mark.parametrize('edit,code',[
    (lambda s:s.update(primitives=[]),'GPU_VIEWER_GEOMETRY_MISSING'),
    (lambda s:s['primitives'].append(s['primitives'][0]),'GPU_VIEWER_DUPLICATE_CELL'),
    (lambda s:s['primitives'][0].__setitem__(0,True),'GPU_VIEWER_COORDINATE_INVALID'),
    (lambda s:s['primitives'][0].__setitem__(3,-1),'GPU_VIEWER_MATERIAL_INVALID'),
    (lambda s:s.update(previewPalette=['not-a-color']),'GPU_VIEWER_PALETTE_INVALID'),
])
def test_bad_data_fails_closed(edit,code):
    data=scene();edit(data)
    with pytest.raises(GPUViewerError, match=code):
        compact_display_data(data)


def test_bad_slice_is_rejected_and_good_slice_keeps_full_voxels():
    with pytest.raises(GPUViewerError, match='GPU_VIEWER_SECTION_INVALID'):
        compact_display_data(scene(), cut_level=9999)
    data=compact_display_data(scene(),cut_level=0)
    assert len(data['voxels'])==3


def test_windows_onedir_includes_viewer_assets_and_integrity_manifest():
    root=Path(__file__).resolve().parents[1]
    bat=(root/'build_windows.bat').read_bytes()
    assert b'--add-data "app\\gpu_viewer_assets;app\\gpu_viewer_assets"' in bat
    from app.release_audit import BASE_FILES
    assert '_internal/app/gpu_viewer_assets/viewer.html' in BASE_FILES
    assert '_internal/app/gpu_viewer_assets/viewer.js' in BASE_FILES


@pytest.mark.skipif(os.name!='nt' and not os.getenv('DISPLAY'),reason='Tk needs display')
def test_real_app_gpu_button_tracks_preview_and_uses_current_section(studio,monkeypatch):
    from app import main
    app,_=studio
    viewed=[]
    monkeypatch.setattr(main,'open_offline_preview',lambda scene,root,cut_level=None:viewed.append((scene,root,cut_level)))
    app._refresh_buttons()
    assert str(app.gpu_view_btn['state'])=='normal'
    app.section_enabled.set(True)
    app._section_changed()
    app.open_gpu_preview()
    assert len(viewed)==1
    assert viewed[0][0] is app.preview.scene
    assert viewed[0][2]==app.preview.cut_level
    app.preview.show(None)
    assert str(app.gpu_view_btn['state'])=='disabled'


def test_actual_gpu_viewer_javascript_contract_with_node():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js unavailable; script remains standalone')
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run([node, str(root / 'tests/gpu_viewer_js_smoke.cjs')],
                         cwd=str(root), capture_output=True, text=True, timeout=15)
    assert run.returncode == 0, (run.stdout + run.stderr)
    assert 'PASS_GPU_VIEWER_JS_MESH_AND_CONTROLS' in run.stdout
