"""Narrow-window real Tk layout, not a Windows EXE or model inference test."""
from __future__ import annotations
import os
import pytest
from app import core
from app import main as studio_main


@pytest.mark.parametrize('width,compact', [(990, True), (1080, True), (1270, False)])
def test_preview_actions_and_six_views_remain_visible_without_overlap(tmp_path, monkeypatch, width, compact):
    if os.name != 'nt' and not os.environ.get('DISPLAY'):
        pytest.skip('Requires a Tk display')
    class Manager:
        root = tmp_path
        state = {'tracked_branch': core.DEFAULT_BRANCH}
        active_path = None
        active_sha = None

    monkeypatch.setattr(studio_main, 'EngineManager', Manager)
    monkeypatch.delenv('MAB_STUDIO_NATIVE', raising=False)
    app = studio_main.Studio()
    try:
        app.geometry(f'{width}x830+0+0')
        app.update()
        assert app._compact_layout is compact
        right_edge = app.viewer_host.winfo_rootx() + app.viewer_host.winfo_width()
        widgets = [app.gpu_view_btn, app.restore_btn, app.export_btn,
                   *app._preview_actions.winfo_children(), *app.camera_buttons.values()]
        for widget in widgets:
            assert widget.winfo_ismapped(), str(widget)
            assert widget.winfo_rootx() + widget.winfo_width() <= right_edge, str(widget)

        def overlap(a, b):
            ax0, ay0 = a.winfo_rootx(), a.winfo_rooty()
            bx0, by0 = b.winfo_rootx(), b.winfo_rooty()
            return (max(ax0,bx0) < min(ax0+a.winfo_width(), bx0+b.winfo_width()) and
                    max(ay0,by0) < min(ay0+a.winfo_height(), by0+b.winfo_height()))

        # The top-row actions previously collided with each other at 990px.
        buttons = [app.restore_btn, app.export_btn, app.gpu_view_btn]
        for i, first in enumerate(buttons):
            assert not any(overlap(first, other) for other in buttons[i+1:])
        camera = list(app.camera_buttons.values())
        for i, first in enumerate(camera):
            assert not any(overlap(first, other) for other in camera[i+1:])
        assert app.viewer_host.winfo_height() >= 200
    finally:
        app.destroy()

@pytest.mark.parametrize('width,height,scale', [
    (1270, 830, 1.5), (1270, 830, 2.0), (990, 690, 1.75),
])
def test_creation_buttons_stay_inside_panel_on_high_dpi(tmp_path, monkeypatch,
                                                       width, height, scale):
    """Regression: Windows photo showed all actions clipped below the prompt."""
    if os.name != 'nt' and not os.environ.get('DISPLAY'):
        pytest.skip('Requires a Tk display')
    from tkinter import Tk
    original_init = Tk.__init__

    def scaled_init(root, *args, **kwargs):
        original_init(root, *args, **kwargs)
        root.tk.call('tk', 'scaling', scale)

    monkeypatch.setattr(Tk, '__init__', scaled_init)

    class Manager:
        root = tmp_path
        state = {'tracked_branch': core.DEFAULT_BRANCH}
        active_path = None
        active_sha = None

    monkeypatch.setattr(studio_main, 'EngineManager', Manager)
    monkeypatch.delenv('MAB_STUDIO_NATIVE', raising=False)
    app = studio_main.Studio()
    try:
        app.geometry(f'{width}x{height}+0+0')
        app.update()
        # The old layout clipped the Generate and Revise buttons outside
        # the fixed-height sidebar even when winfo_ismapped() was True.
        left = app._creation_actions.master
        top = left.winfo_rooty()
        bottom = top + left.winfo_height()
        for widget in (app.generate_btn, app.revise_btn, app.sample_btn):
            assert widget.winfo_ismapped()
            assert top <= widget.winfo_rooty()
            assert widget.winfo_rooty() + widget.winfo_height() <= bottom
        assert app.prompt.winfo_height() >= 65
        assert app.prompt.winfo_rooty() + app.prompt.winfo_height() <= app.generate_btn.winfo_rooty()
    finally:
        app.destroy()


def test_ctrl_enter_submits_once_without_changing_source(tmp_path, monkeypatch):
    if os.name != 'nt' and not os.environ.get('DISPLAY'):
        pytest.skip('Requires a Tk display')

    class Manager:
        root = tmp_path
        state = {'tracked_branch': core.DEFAULT_BRANCH}
        active_path = None
        active_sha = None

    monkeypatch.setattr(studio_main, 'EngineManager', Manager)
    monkeypatch.delenv('MAB_STUDIO_NATIVE', raising=False)
    app = studio_main.Studio()
    try:
        app.update()
        calls = []
        app.generate_btn.configure(command=lambda: calls.append('generate'))
        app.prompt.focus_force()
        app.prompt.event_generate('<Control-Return>')
        app.update()
        assert calls == ['generate']
        app.generate_btn.state(['disabled'])
        app.prompt.event_generate('<Control-Return>')
        app.update()
        assert calls == ['generate']
    finally:
        app.destroy()
