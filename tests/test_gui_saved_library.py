"""Actual Tk interactions for reopening earlier design projects (fake MAB)."""
from __future__ import annotations
import copy
import os
import time
import tkinter as tk
from tkinter import ttk
import pytest
from app import core
from app import main as studio_main
from app.examples import example_library
from tests.test_gui_review_flow import _scene


def pump(app, seconds=4):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        app.update()
        if not app.busy:
            app.update()
            return
        time.sleep(.02)
    raise AssertionError('GUI worker did not finish')


def test_choose_earlier_building_in_real_tk(tmp_path, monkeypatch):
    if os.name!='nt' and not os.getenv('DISPLAY'):
        pytest.skip('Tk display not available')
    class Manager:
        root=tmp_path
        active_sha=core.BASELINE_SHA
        active_path=tmp_path/'fixed-engine'
        state={'tracked_branch':core.DEFAULT_BRANCH}
    monkeypatch.setattr(studio_main, 'EngineManager', Manager)
    one=example_library()
    two=copy.deepcopy(one);two['title']='Second test building'
    scenes={one['title']:_scene(one),two['title']:_scene(two,'glass')}
    monkeypatch.setattr(studio_main,'run_bridge',lambda engine, design: copy.deepcopy(scenes[design['title']]))
    app=studio_main.Studio()
    try:
        app._display_design(one,scenes[one['title']],'FIXTURE','','initial')
        first=app.history.head()['revisionId']
        app._display_design(two,scenes[two['title']],'FIXTURE','','other')
        last=app.history.head()['revisionId']
        assert first!=last
        assert app.design['title']==two['title']
        app.browse_saved_designs()
        pump(app)
        popups=[x for x in app.winfo_children() if isinstance(x,tk.Toplevel)]
        assert len(popups)==1
        modal=popups[0]
        def find_tree(widget):
            if isinstance(widget,ttk.Treeview):return widget
            return next((t for c in widget.winfo_children() if (t:=find_tree(c)) is not None),None)
        tree=find_tree(modal)
        assert tree is not None
        assert len(tree.get_children())==2
        tree.selection_set(first)
        buttons=[]
        def find_buttons(widget):
            for child in widget.winfo_children():
                if isinstance(child,ttk.Button):buttons.append(child)
                find_buttons(child)
        find_buttons(modal)
        button=next(b for b in buttons if b.cget('text')=='選択した設計を開く')
        button.invoke()
        pump(app)
        assert app.design['title']==one['title']
        assert app.history.head()['revisionId']==first
        assert len(app.history.list_saved())==2
        assert 'disabled' not in app.browse_btn.state()
        assert 'disabled' not in app.export_btn.state()
        assert app.terrain is None
    finally:
        for task in app.tk.call('after','info'):
            try:app.after_cancel(task)
            except Exception:pass
        app.destroy()
