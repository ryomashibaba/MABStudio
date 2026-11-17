"""Real Tk button wiring with OFFLINE transport stub; no Minecraft connection."""
import os
import time
import pytest
from app import main as studio_main


@pytest.mark.skipif(os.name!='nt' and not os.getenv('DISPLAY'),reason='GUI needs X server')
def test_education_button_starts_readonly_and_restores_ui(tmp_path,monkeypatch):
    class Manager:
        root=tmp_path
        active_sha=None
        active_path=None
        state={'tracked_branch':studio_main.DEFAULT_BRANCH}
    monkeypatch.setattr(studio_main,'EngineManager',Manager)
    infos=[]
    monkeypatch.setattr(studio_main.messagebox,'askyesno',lambda *a,**k:True)
    monkeypatch.setattr(studio_main.messagebox,'showinfo',lambda *a,**k:infos.append(a))
    monkeypatch.setattr(studio_main,'run_education_readonly',lambda coords,stop:{
        'coords':dict(zip(('x','y','z'),coords)),
        'outcome':'INCONCLUSIVE_NEEDS_GAME_INSPECTION',
        'provisionalStateValue':None,'worldWrites':0,
        'readbackPath':str(tmp_path/'test-only.json')})
    root=studio_main.Studio()
    try:
        root.place_xyz.set('1 64 2')
        root.start_education_readonly()
        assert len(infos)>=1
        # Thread work may be finished; process queue with actual Tk event loop.
        deadline=time.monotonic()+2
        while root.busy and time.monotonic()<deadline:
            root.update()
            time.sleep(0.02)
        assert root.busy is False
        assert root._education_running is False
        assert root.edu_live_btn.instate(['!disabled'])
        assert root.edu_stop_btn.instate(['disabled'])
        assert len(infos)==2
        assert 'UNKNOWN' not in root.status.get()  # Diagnostic not invented.
    finally:
        root.destroy()


@pytest.mark.skipif(os.name!='nt' and not os.getenv('DISPLAY'),reason='GUI needs X server')
def test_education_invalid_xyz_does_not_start(tmp_path,monkeypatch):
    class Manager:
        root=tmp_path
        active_sha=None
        active_path=None
        state={'tracked_branch':studio_main.DEFAULT_BRANCH}
    monkeypatch.setattr(studio_main,'EngineManager',Manager)
    errors=[]
    monkeypatch.setattr(studio_main.messagebox,'showerror',lambda *a:errors.append(a))
    root=studio_main.Studio()
    try:
        root.place_xyz.set('1 1000 2')
        root.start_education_readonly()
        assert errors
        assert root._education_running is False
        assert root.busy is False
    finally:
        root.destroy()
