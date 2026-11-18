"""One-session Tk button journey with isolated local fixtures, NOT real AI/Windows QA."""
from __future__ import annotations
import json
import tkinter as tk
from tkinter import ttk
from app import main as studio_main
from tests.test_gui_review_flow import studio, _complete


def test_click_review_reject_approve_export_restart_and_browse(studio, monkeypatch, tmp_path):
    app, _calls = studio
    baseline = app.history.head()
    original = (app.manager.root / 'outputs' / 'latest-design.json').read_bytes()
    # The actual 'open last design' action must preserve the saved head.
    app.load_btn.invoke()
    _complete(app)
    assert app.current_snapshot['revisionId'] == baseline['revisionId']
    assert app.preview.scene is not None
    app.revise_btn.invoke()
    _complete(app)
    assert app.pending_revision is not None
    assert app.history.head()['revisionId'] == baseline['revisionId']
    assert (app.manager.root / 'outputs' / 'latest-design.json').read_bytes() == original
    assert app.reviewbar.winfo_ismapped()

    app.reject_draft_btn.invoke()
    assert app.pending_revision is None
    assert app.history.head()['revisionId'] == baseline['revisionId']
    assert (app.manager.root / 'outputs' / 'latest-design.json').read_bytes() == original
    app.revise_btn.invoke()
    _complete(app)
    app.approve_draft_btn.invoke()
    _complete(app)
    approved = app.history.head()
    assert approved['parentId'] == baseline['revisionId']
    assert approved['worldWrites'] == 0
    assert app.design['title'] == approved['design']['title']
    assert app.pending_revision is None

    export = tmp_path / 'approved-source.json'
    monkeypatch.setattr(studio_main.filedialog, 'asksaveasfilename', lambda **kw: str(export))
    monkeypatch.setattr(studio_main.messagebox, 'showinfo', lambda *args: None)
    app.export_btn.invoke()
    _complete(app)
    assert json.loads(export.read_text(encoding='utf-8')) == approved['design']

    app.destroy()
    restarted = studio_main.Studio()
    monkeypatch.setattr(restarted, 'refresh_models', lambda: None)  # no inference/network
    try:
        restarted._restore_last_session()
        _complete(restarted)
        assert restarted.current_snapshot['revisionId'] == approved['revisionId']
        assert restarted.scene['sourceSha256'] == approved['sourceSha256']
        assert restarted.history.head()['revisionId'] == approved['revisionId']

        # Open via the actual top-level 'saved buildings' button and dialog.
        top_button = next(w for w in restarted.winfo_children()[0].winfo_children()[0].winfo_children()
                          if isinstance(w, ttk.Button) and w.cget('text') == '保存した建築')
        top_button.invoke()
        _complete(restarted)
        dialogs = [w for w in restarted.winfo_children()
                   if isinstance(w, tk.Toplevel) and w.winfo_exists() and w.winfo_viewable()]
        assert len(dialogs) == 1
        dialog = dialogs[0]
        def descendants(parent):
            for child in parent.winfo_children():
                yield child
                yield from descendants(child)
        tree = next(w for w in descendants(dialog) if isinstance(w, ttk.Treeview))
        assert baseline['revisionId'] in tree.get_children()
        tree.selection_set(baseline['revisionId'])
        open_button = next(w for w in descendants(dialog)
                           if isinstance(w, ttk.Button) and w.cget('text') == '選択した設計を開く')
        open_button.invoke()
        _complete(restarted)
        assert restarted.current_snapshot['revisionId'] == baseline['revisionId']
        assert restarted.scene['sourceSha256'] == baseline['sourceSha256']
        assert restarted.history.head()['revisionId'] == baseline['revisionId']
        assert approved['revisionId'] in {item['revisionId'] for item in restarted.history.list_saved()}
        assert len(restarted.history.list_saved()) == 2
    finally:
        restarted.destroy()
