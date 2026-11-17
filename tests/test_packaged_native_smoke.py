"""The actual frozen GUI defaults to VTK, not a Tk Canvas.

These checks are test-double GUI objects only; they cannot certify a Windows EXE.
"""
import json
from pathlib import Path
import sys

import pytest

from app import smoke


class NativeStub:
    def __init__(self, *, visible=917):
        self._visible_count = visible
        self._photo = object()
        self._rendered_size = (640, 480)
        self.draw_calls = 0

    def show(self, scene):
        self.scene = scene

    def draw(self):
        self.draw_calls += 1


class CanvasStub:
    detail_limit = 1000

    def show(self, scene):
        self.scene = scene

    def find_all(self):
        return [None] * 12


def _run(tmp_path, monkeypatch, preview, *, frozen):
    from app import native_viewer, native_selfcheck
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    monkeypatch.setattr(smoke, 'verify_packaged_runtime', lambda: {'packagedRuntimeVerified': frozen})
    monkeypatch.setattr(smoke, 'node_binary', lambda: '/safe/node.exe')
    monkeypatch.setattr(smoke, 'compatibility_check', lambda _: {'verified': True})
    monkeypatch.setattr(smoke, 'run_bridge', lambda *_: {
        'stats': {'voxels': 917}, 'geometrySha256': '8'*64, 'primitives': []})
    monkeypatch.setattr(smoke, 'example_library', lambda: {})
    monkeypatch.setattr(native_viewer, 'NativeVoxelPreview', NativeStub)
    monkeypatch.setattr(native_selfcheck, '_image_signature', lambda viewer: 'f'*64)
    class FakeStudio:
        def __init__(self):
            self.preview = preview
            self.closed = False
        def update_idletasks(self):
            pass
        def destroy(self):
            self.closed = True
    report = tmp_path / 'result.json'
    result = smoke.run_smoke(FakeStudio, ['--smoke', '--smoke-engine', str(tmp_path),
                                          '--smoke-report', str(report)])
    return result, json.loads(report.read_text(encoding='utf-8'))


def test_frozen_native_embedded_917_cells_pass(tmp_path, monkeypatch):
    native = NativeStub()
    code, receipt = _run(tmp_path, monkeypatch, native, frozen=True)
    assert code == 0
    assert receipt['result'] == 'PASS'
    assert receipt['guiRenderer'] == 'VTK_NATIVE_EMBEDDED'
    assert receipt['nativeCells'] == 917
    assert receipt['nativeScenePixelsVerified']
    assert len(receipt['nativePixelSignatureSha256']) == 64
    assert native.draw_calls == 1
    assert receipt['worldWrites'] == receipt['worldContacts'] == 0


def test_frozen_lightweight_fallback_fails_release_qa(tmp_path, monkeypatch):
    code, receipt = _run(tmp_path, monkeypatch, CanvasStub(), frozen=True)
    assert code == 1
    assert receipt['result'] == 'FAIL'
    assert 'PACKAGED_NATIVE_VIEWER_NOT_ACTIVE' in receipt['error']


def test_source_lightweight_is_explicitly_not_native(tmp_path, monkeypatch):
    code, receipt = _run(tmp_path, monkeypatch, CanvasStub(), frozen=False)
    assert code == 0
    assert receipt['guiRenderer'] == 'TK_LIGHTWEIGHT_SOURCE_ONLY'
    assert receipt['nativeScenePixelsVerified'] is False


def test_native_wrong_source_cell_count_fails(tmp_path, monkeypatch):
    code, receipt = _run(tmp_path, monkeypatch, NativeStub(visible=916), frozen=True)
    assert code == 1
    assert 'PACKAGED_NATIVE_SOURCE_SCENE_NOT_VISIBLE' in receipt['error']


def test_both_windows_build_paths_reextract_and_test_zip():
    root = Path(__file__).resolve().parents[1]
    qa = (root / 'RUN_WINDOWS_NATIVE_QA.ps1').read_text(encoding='utf-8-sig')
    ci = (root / '.github/workflows/windows-build.yml').read_text(encoding='utf-8')
    for text in (qa, ci):
        assert 'ExtractToDirectory' in text
        assert 'PORTABLE_EXE' in text or 'PORTABLE_RELEASE_EXE' in text
        assert 'VTK_NATIVE_EMBEDDED' in text
        assert 'nativeScenePixelsVerified' in text
        assert "'--smoke'" in text
        assert "'--native-selfcheck'" in text
        assert 'WaitForExit(120000)' in text
    assert qa.index('CreateFromDirectory') < qa.index('ExtractToDirectory')
    assert ci.index('CreateFromDirectory') < ci.index('ExtractToDirectory')


def test_real_vtk_917_synthetic_cells_follow_gui_smoke_path(tmp_path, monkeypatch):
    """Linux display smoke: actual embedded VTK pixels, not just a mocked class.

    This scene is SYNTHETIC; real pinned B143 and Windows EXE are separate gates.
    """
    import os
    import tkinter as tk
    from app import native_viewer
    if os.name != 'nt' and not os.environ.get('DISPLAY'):
        pytest.skip('Needs Xvfb/display')
    if not native_viewer.native_dependencies_available():
        pytest.skip('Native VTK/Pillow not available')
    cells = [[x, y, z, (x + y + z) % 3] for x in range(13)
             for y in range(11) for z in range(7)][:917]
    scene = {'primitives': cells,
             'previewPalette': ['#c7a37d', '#698caa', '#a6c4ab'],
             'stats': {'voxels': 917}, 'geometrySha256': 'b'*64}
    monkeypatch.setattr(smoke, 'verify_packaged_runtime', lambda: {'mode': 'SOURCE', 'packagedRuntimeVerified': False})
    monkeypatch.setattr(smoke, 'node_binary', lambda: 'NO_PROCESS_LAUNCHED')
    monkeypatch.setattr(smoke, 'compatibility_check', lambda _: {'synthetic': True})
    monkeypatch.setattr(smoke, 'run_bridge', lambda *_: scene)
    monkeypatch.setattr(smoke, 'example_library', lambda: {})

    class EmbeddedStudio(tk.Tk):
        def __init__(self):
            super().__init__()
            self.geometry('1024x760+4+4')
            self.preview = native_viewer.NativeVoxelPreview(self)
            self.preview.pack(fill='both', expand=True)
        def destroy(self):
            self.preview.destroy()
            super().destroy()

    report_path = tmp_path / 'actual-native-frame.json'
    result = smoke.run_smoke(EmbeddedStudio, [
        '--smoke', '--smoke-engine', str(tmp_path), '--smoke-report', str(report_path)])
    receipt = json.loads(report_path.read_text(encoding='utf-8'))
    assert result == 0, receipt.get('error')
    assert receipt['guiRenderer'] == 'VTK_NATIVE_EMBEDDED'
    assert receipt['nativeScenePixelsVerified']
    assert receipt['nativeCells'] == 917
    assert min(receipt['nativeFramebuffer']) >= 100
    assert receipt['worldContacts'] == receipt['worldWrites'] == 0
