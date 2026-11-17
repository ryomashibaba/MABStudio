"""MAB Studio Windows GUI. Offline-first, test-world only. No world-writing calls."""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.core import (
    BASELINE_SHA, DEFAULT_BRANCH, EngineManager, StudioError,
    ask_llm, branch_sha, compatibility_check, llm_models, run_bridge, atomic_json
)
from app.preview import VoxelPreview
from app.examples import example_library
from app.revisions import RevisionStore, compare_scenes, diff_preview_scene
from app.terrain import inspect_mcworld, read_mcworld_terrain, create_overlay_scene


class Studio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('MAB Studio — Minecraft Education 建築AI（安全プレビュー）')
        self.geometry('1210x880')
        self.minsize(1020, 840)
        self.configure(bg='#e9eef5')
        self.manager = EngineManager()
        self.pending_sha = None
        self.pending_events = queue.Queue()
        self.busy = False
        self.scene = None
        self.design = None
        self.history = RevisionStore(self.manager.root / 'projects')
        self.current_snapshot = None
        self.delta = None
        self.terrain = None
        self.terrain_archive = None
        self.terrain_report = None
        self.view_mode = tk.StringVar(value='建築のみ')
        self.detail_mode = tk.StringVar(value='標準（2,300）')
        self.place_xyz = tk.StringVar(value='0 64 0')
        self.llm_provider = tk.StringVar(value='Ollama')
        self.model = tk.StringVar(value='')
        self.branch = tk.StringVar(value=self.manager.state.get('tracked_branch', DEFAULT_BRANCH))
        self._make_ui()
        self.after(120, self._poll)
        self.after(2000, self._startup)
        self.after(15*60*1000, self._periodic_check)

    def _make_ui(self):
        style = ttk.Style(self)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('Title.TLabel', font=('Yu Gothic UI',17,'bold'), foreground='#15304d')
        style.configure('Header.TLabel', font=('Yu Gothic UI',10,'bold'))
        style.configure('TButton', font=('Yu Gothic UI',10), padding=(9,7))
        style.configure('TLabel', font=('Yu Gothic UI',10))
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer,text='MAB Studio',style='Title.TLabel').pack(anchor='w')
        ttk.Label(outer,text='自然な日本語 → ローカルAI → MAB SOURCE検証 → 3Dプレビュー  |  ワールド操作なし').pack(anchor='w',pady=(0,10))
        body = ttk.Frame(outer)
        body.pack(fill='both',expand=True)
        body.columnconfigure(0,minsize=385,weight=0)
        body.columnconfigure(1,weight=1)
        body.rowconfigure(0,weight=1)
        left = ttk.Frame(body,padding=(0,5,12,0),width=390)
        right = ttk.Frame(body,padding=(8,5,0,0))
        left.grid(row=0,column=0,sticky='nsew')
        left.grid_propagate(False)
        right.grid(row=0,column=1,sticky='nsew')
        left.columnconfigure(0,weight=1)
        self.engine_state=tk.StringVar(value='エンジンを確認中')
        ttk.Label(left,text='1  MABエンジン',style='Header.TLabel').grid(row=0,column=0,sticky='w')
        ttk.Label(left,textvariable=self.engine_state,wraplength=330,foreground='#2a516e').grid(row=1,column=0,sticky='w',pady=(3,5))
        self.install_btn=ttk.Button(left,text='MABを初期導入（確認済みB143）',command=self.install_baseline)
        self.install_btn.grid(row=2,column=0,sticky='ew')
        branchrow=ttk.Frame(left)
        branchrow.grid(row=3,column=0,sticky='ew',pady=(9,4))
        branchrow.columnconfigure(0,weight=1)
        ttk.Entry(branchrow,textvariable=self.branch).grid(row=0,column=0,sticky='ew',padx=(0,4))
        ttk.Button(branchrow,text='追跡保存',command=self.save_branch).grid(row=0,column=1)
        updates=ttk.Frame(left)
        updates.grid(row=4,column=0,sticky='ew',pady=(0,5))
        updates.columnconfigure(0,weight=1)
        updates.columnconfigure(1,weight=1)
        self.check_btn=ttk.Button(updates,text='GitHub更新を確認',command=self.check_update)
        self.check_btn.grid(row=0,column=0,sticky='ew',padx=(0,4))
        self.apply_btn=ttk.Button(updates,text='検証して更新',command=self.install_pending,state='disabled')
        self.apply_btn.grid(row=0,column=1,sticky='ew')
        self.rollback_btn=ttk.Button(left,text='前のエンジンに戻す',command=self.rollback)
        self.rollback_btn.grid(row=5,column=0,sticky='ew')
        ttk.Separator(left).grid(row=6,column=0,sticky='ew',pady=13)
        ttk.Label(left,text='2  ローカルAI',style='Header.TLabel').grid(row=7,column=0,sticky='w')
        provider=ttk.Combobox(left,textvariable=self.llm_provider,values=('Ollama','LM Studio'),state='readonly')
        provider.grid(row=8,column=0,sticky='ew',pady=(5,4))
        provider.bind('<<ComboboxSelected>>',lambda _: self.refresh_models())
        mrow=ttk.Frame(left)
        mrow.grid(row=9,column=0,sticky='ew',pady=(0,7))
        mrow.columnconfigure(0,weight=1)
        self.model_box=ttk.Combobox(mrow,textvariable=self.model)
        self.model_box.grid(row=0,column=0,sticky='ew',padx=(0,4))
        self.models_btn=ttk.Button(mrow,text='モデル取得',command=self.refresh_models)
        self.models_btn.grid(row=0,column=1)
        ttk.Label(left,text='3  どんな建築を作りたい？',style='Header.TLabel').grid(row=10,column=0,sticky='w',pady=(6,5))
        self.prompt = tk.Text(left,height=7,wrap='word',font=('Yu Gothic UI',10),relief='solid',borderwidth=1)
        self.prompt.grid(row=11,column=0,sticky='nsew')
        self.prompt.insert('1.0','未来的な2階建ての小さな図書館。ガラス張りの正面、\n広い入口、屋上庭園を作って。')
        left.rowconfigure(11,weight=1)
        self.generate_btn=ttk.Button(left,text='AIで設計して3Dプレビュー',command=self.generate)
        self.generate_btn.grid(row=12,column=0,sticky='ew',pady=(10,4))
        self.revise_btn=ttk.Button(left,text='現在の設計をAIで部分修正',command=self.revise_design,state='disabled')
        self.revise_btn.grid(row=13,column=0,sticky='ew',pady=(2,4))
        self.sample_btn=ttk.Button(left,text='サンプル建築で動作確認（AI不要）',command=self.show_sample)
        self.sample_btn.grid(row=14,column=0,sticky='ew',pady=(2,4))
        self.export_btn=ttk.Button(left,text='検証済み設計をJSON保存',command=self.export_design,state='disabled')
        self.export_btn.grid(row=15,column=0,sticky='ew',pady=(2,4))
        self.edu_btn=ttk.Button(left,text='Educationへインポート（実機未検証）',state='disabled')
        self.edu_btn.grid(row=16,column=0,sticky='ew',pady=(2,5))
        ttk.Label(left,text='注意：B143は設計用。教育版への施工・ファイル書込は未接続。\n専用テストワールド以外では使わないでください。',
                  wraplength=320,foreground='#a0522d').grid(row=17,column=0,sticky='w',pady=(2,3))
        ttk.Label(right,text='SOURCE建築モデル / 設計差分 / ワールド地形（読み取り専用）',style='Header.TLabel').pack(anchor='w',pady=(0,6))
        tools = ttk.Frame(right)
        tools.pack(fill='x', pady=(0, 7))
        self.view_box = ttk.Combobox(tools,textvariable=self.view_mode, state='readonly', width=17,
                                     values=('建築のみ','変更差分','地形＋建築'))
        self.view_box.pack(side='left',padx=(0,6))
        detail_box = ttk.Combobox(tools, textvariable=self.detail_mode, state='readonly',
                                  width=16, values=tuple(VoxelPreview.DETAIL_LIMITS))
        detail_box.pack(side='left',padx=(0,6))
        detail_box.bind('<<ComboboxSelected>>', lambda _: self.preview.set_detail(self.detail_mode.get()))
        self.view_box.bind('<<ComboboxSelected>>', lambda _: self._refresh_view())
        self.restore_btn=ttk.Button(tools,text='前の設計に戻す',command=self.restore_previous,state='disabled')
        self.restore_btn.pack(side='left',padx=(0,6))
        ttk.Button(tools,text='前回の設計を開く',command=self.load_saved_design).pack(side='left')
        terrainbar = ttk.Frame(right)
        terrainbar.pack(fill='x', pady=(0,7))
        ttk.Label(terrainbar,text='予定位置 XYZ').pack(side='left')
        ttk.Entry(terrainbar,textvariable=self.place_xyz,width=18).pack(side='left',padx=(6,6))
        self.import_terrain_btn=ttk.Button(terrainbar,text='.mcworldを読み取る',command=self.import_terrain)
        self.import_terrain_btn.pack(side='left')
        self.preview=VoxelPreview(right)
        self.preview.pack(fill='both',expand=True)
        self.caption=tk.StringVar(value='表示待ち：AIモデル・エンジンが準備できたら生成できます。')
        ttk.Label(right,textvariable=self.caption,wraplength=670).pack(anchor='w',pady=(7,0))
        self.status=tk.StringVar(value='準備中')
        foot=ttk.Label(outer,textvariable=self.status,anchor='w',wraplength=950,foreground='#34566e')
        foot.pack(fill='x',pady=(9,0))

    def _startup(self):
        self._update_engine_label()
        # First launch: automatically install ONLY the SHA-pinned, code-hash-
        # verified B143. Failed/offline installs leave the manual retry button.
        # Do not auto-install moving branches, world adapters or model packages.
        if self.manager.active_path:
            self.refresh_models()
            self.after(9000, lambda: self.check_update(silent=True))
        else:
            def ready(_):
                self._invalidate_scene()
                self._update_engine_label()
                self.caption.set('安全確認済みB143の初期導入完了。建築を指示できます。')
                self.refresh_models()
            self._start('初回起動：固定B143を安全確認して導入中',
                        lambda: self.manager.install(BASELINE_SHA), ready, quiet=True)

    def _periodic_check(self):
        if not self.busy and self.manager.active_path:
            self.check_update(silent=True)
        self.after(15*60*1000,self._periodic_check)

    def _update_engine_label(self):
        sha=self.manager.active_sha
        self.engine_state.set('使用中: '+sha[:12]+'…  /  更新はコミットSHA固定' if self.manager.active_path
                              else '未導入 / 最初にB143を導入してください')
        self.generate_btn.configure(state='normal' if self.manager.active_path and not self.busy else 'disabled')
        self.sample_btn.configure(state='normal' if self.manager.active_path and not self.busy else 'disabled')
        self.install_btn.configure(state='normal' if not self.busy and not self.manager.active_path else 'disabled')
        self.check_btn.configure(state='normal' if not self.busy else 'disabled')
        self.rollback_btn.configure(state='normal' if not self.busy else 'disabled')
        self.apply_btn.configure(state='normal' if self.pending_sha and not self.busy else 'disabled')
        self._refresh_buttons()

    def _start(self, title, fn, callback=None, quiet=False):
        if self.busy:
            return
        self.busy=True
        self.status.set(title)
        self._update_engine_label()
        def work():
            try:
                result=fn()
                self.pending_events.put(('ok',title,result,callback,quiet))
            except Exception as e:
                self.pending_events.put(('error',title,str(e),callback,quiet))
        threading.Thread(target=work,daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind,title,result,callback,quiet=self.pending_events.get_nowait()
                self.busy=False
                if kind=='error':
                    if quiet and title.startswith('GitHub'):
                        self.status.set('GitHub更新確認はオフラインです。導入済みエンジンは使えます。')
                    else:
                        self.status.set('失敗: '+str(result))
                    if not quiet:messagebox.showerror(title,str(result))
                else:
                    self.status.set('完了: '+title)
                    if callback:
                        try:callback(result)
                        except Exception as e:
                            self.status.set('表示に失敗: '+str(e))
                self._update_engine_label()
        except queue.Empty:
            pass
        self.after(120,self._poll)

    def _invalidate_scene(self):
        self.scene = None
        self.preview.show(None)
        self.export_btn.configure(state='disabled')
        self.delta = None
        self.design = None
        self.current_snapshot = None
        self._refresh_buttons()

    def _refresh_buttons(self):
        self.revise_btn.configure(state='normal' if self.design and self.manager.active_path and not self.busy else 'disabled')
        self.restore_btn.configure(state='normal' if self.current_snapshot and self.current_snapshot.get('parentId') and not self.busy else 'disabled')

    def _coords(self):
        try:
            tokens = self.place_xyz.get().replace(',', ' ').split()
            if len(tokens) != 3:
                raise ValueError('expected 3')
            coordinates = tuple(int(t) for t in tokens)
            if any(abs(x) > 30000000 for x in coordinates):
                raise ValueError('beyond bounds')
            return coordinates
        except ValueError as exc:
            raise StudioError('予定位置は X Y Z の整数3個で入力してください') from exc

    def _refresh_view(self):
        if not self.scene:
            self.preview.show(None)
            return
        mode = self.view_mode.get()
        if mode == '変更差分':
            if self.delta is None or not any(self.delta['counts'][p] for p in ('added','removed','replaced')):
                self.preview.show(self.scene)
                self.status.set('変更差分がありません。通常モデルを表示します。')
            else:
                self.preview.show(diff_preview_scene(self.delta, self.scene))
        elif mode == '地形＋建築':
            if self.terrain is None:
                self.preview.show(self.scene)
                self.status.set('地形ブロックは未読取です。.mcworldの読み込みを先に行ってください')
            else:
                try:
                    merged, self.terrain_report = create_overlay_scene(self.terrain, self.scene, self._coords())
                    self.preview.show(merged)
                    self.status.set('実地形との重なり候補: ' + str(self.terrain_report['potentialTerrainIntersections']) +
                                    'セル / 不明な地形セル: ' + str(self.terrain_report['unreadableCells']) + '（未施工）')
                except StudioError as exc:
                    self.preview.show(self.scene)
                    self.status.set(str(exc))
        else:
            self.preview.show(self.scene)

    def import_terrain(self):
        filename = filedialog.askopenfilename(title='Minecraft Educationからエクスポートした.mcworldを選択',
                                              filetypes=[('Minecraft World','*.mcworld')])
        if not filename:
            return
        try: center = self._coords()
        except StudioError as e:
            messagebox.showerror('予定位置XYZ',str(e)); return
        def work():
            metadata = inspect_mcworld(filename)
            try:
                terrain = read_mcworld_terrain(metadata, center, size=24, height=24)
                return metadata, terrain, None
            except StudioError as exc:
                return metadata, None, str(exc)
        def done(result):
            metadata, terrain, issue = result
            self.terrain_archive, self.terrain = metadata, terrain
            self.view_mode.set('地形＋建築')
            if terrain is None:
                self.status.set('ZIP検査成功: ' + metadata['worldName'] + '。地形は未読取: ' + str(issue))
                messagebox.showwarning('地形ブロックは未読取',
                                       'エクスポートされたワールド構造を確認しました。\nただし、地形ブロックはまだ取得できていません。\n' + str(issue))
            else:
                self._refresh_view()
                self.caption.set('実地形スナップショット: ' + metadata['worldName'] +
                                 ' / 既知ブロック: ' + str(len(terrain['cells'])) +
                                 ' / 未読取: ' + str(terrain['unknown']) + '（Minecraft未変更）')
        self._start('安全なコピーから.mcworld地形を解析中', work, done)

    def install_baseline(self):
        self._start('B143エンジンを検証して初期導入中',lambda:self.manager.install(BASELINE_SHA),
                    lambda r:(self._invalidate_scene(),self._update_engine_label(),
                              self.caption.set('B143導入とSOURCE互換性試験が完了（施工は無効）')))

    def save_branch(self):
        try:
            prior = self.manager.state.get('tracked_branch', DEFAULT_BRANCH)
            self.manager.save_branch(self.branch.get().strip())
            if prior != self.branch.get().strip():
                self.pending_sha = None
                self._update_engine_label()
            self.status.set('追跡ブランチを保存。アプリは自動で本体を書き換えません。')
        except Exception as e:messagebox.showerror('入力エラー',str(e))

    def check_update(self,silent=False):
        try:self.manager.save_branch(self.branch.get().strip())
        except StudioError as e:
            if not silent:messagebox.showerror('追跡ブランチ',str(e))
            return
        target_branch = self.branch.get().strip()
        def done(sha):
            if self.branch.get().strip() != target_branch:
                self.pending_sha = None
                self.status.set('追跡先が変わったため更新候補を破棄しました。再確認してください。')
                return
            if sha == self.manager.active_sha:
                self.pending_sha=None
                self.status.set('GitHub確認：現在のエンジンは追跡ブランチと同じSHAです。')
            else:
                self.pending_sha=sha
                self.status.set('更新候補: '+sha[:12]+'…  検証して更新を押すまで切り替えません。')
            self._update_engine_label()
        self._start('GitHubの最新コミットを確認中',lambda:branch_sha(target_branch),done,quiet=silent)

    def install_pending(self):
        sha=self.pending_sha
        if not sha or sha==self.manager.active_sha:
            return
        self._start('更新候補を隔離導入しSOURCE互換性を検証中',lambda:self.manager.install(sha),
                    lambda r:(setattr(self,'pending_sha',None),self._invalidate_scene(),
                              self._update_engine_label(),self.caption.set('更新適用済み。以前のエンジンへ復元できます。')))

    def rollback(self):
        self._start('旧エンジンを検証して復元中',self.manager.rollback,
                    lambda sha:(self._invalidate_scene(),self.caption.set('復元しました: '+sha[:12]+'…')))

    def refresh_models(self):
        provider=self.llm_provider.get()
        def done(names):
            self.model_box['values']=names
            if names and self.model.get() not in names:
                self.model.set(names[0])
            self.status.set(f'{provider}: {len(names)}件のモデルを取得しました。')
        self._start(provider+'のモデル一覧を取得中',lambda:llm_models(provider),done,quiet=True)

    def generate(self):
        prompt=self.prompt.get('1.0','end').strip()
        provider=self.llm_provider.get()
        model=self.model.get().strip()
        engine=self.manager.active_path
        if not engine:
            messagebox.showwarning('未導入','MAB B143エンジンを導入してください。')
            return
        if not prompt or not model:
            messagebox.showwarning('入力不足','日本語指示とローカルLLMモデルを選んでください。')
            return
        def work():
            error = ''
            design = None
            # No world writes. Exactly one bounded correction attempt.
            for attempt in range(2):
                previous = design
                try:
                    design = ask_llm(provider, model, prompt, error, previous_design=previous)
                    scene = run_bridge(engine, design)
                    return design, scene, attempt
                except StudioError as e:
                    error = str(e)
            raise StudioError('AIの設計を2回検証しましたが、MAB SOURCEが拒否しました: ' + error)
        def done(res):
            design, scene, retry = res
            self._display_design(design, scene, provider, model,
                                 '自動修正1回' if retry else '初回成功')
        self._start('ローカルAIで設計し、MAB SOURCEで検証中',work,done)

    def show_sample(self):
        engine = self.manager.active_path
        if not engine:
            messagebox.showwarning('未導入','先にMABエンジンを導入してください。')
            return
        doc = example_library()
        def done(scene):
            self._display_design(doc, scene, 'OFFLINE_SAMPLE', '', 'サンプル / AI未使用')
        self._start('本物のMABでサンプル設計をSOURCE検証中',
                    lambda: run_bridge(engine, doc), done)

    def _display_design(self, design, scene, provider, model, qualifier, *, operation='new'):
        if operation == 'revision':
            previous = self.current_snapshot
            if previous is None:
                raise StudioError('修正元の設計履歴がありません')
            delta = compare_scenes(previous['scene'], scene)
            snapshot = self.history.commit(design, scene, engine_commit=self.manager.active_sha,
                                           operation='revision', previous_id=previous['revisionId'])
        else:
            delta = None
            snapshot = self.history.commit(design, scene, engine_commit=self.manager.active_sha,
                                           operation='new')
        self.current_snapshot = snapshot
        self.delta = delta
        self.scene = scene
        self.design = design
        out_dir = self.manager.root/'outputs'
        atomic_json(out_dir/'latest-design.json', design)
        atomic_json(out_dir/'latest-scene.json', scene)
        atomic_json(out_dir/'latest-receipt.json', {
            'appVersion':'0.3.0','createdAt':datetime.now(timezone.utc).isoformat(),
            'engineCommit':self.manager.active_sha,'sourceSha256':scene['sourceSha256'],
            'geometrySha256':scene['geometrySha256'],'provider':provider,'model':model,
            'readiness':'NOT_PREPARED','worldWrites':0})
        self._refresh_view()
        self._refresh_buttons()
        self.caption.set(f'{scene.get("title", "建築")} | {scene["stats"]["voxels"]:,}セル'
                         f' | SOURCE検証成功 | {qualifier} | 施工禁止')
        self.export_btn.configure(state='normal')
        if self.delta is not None:
            self.caption.set(self.caption.get() + ' | 追加' + str(self.delta['counts']['added']) +
                             ' / 削除' + str(self.delta['counts']['removed']) +
                             ' / 交換' + str(self.delta['counts']['replaced']) + '（施工前）')

    def revise_design(self):
        if not self.scene or not self.design or self.current_snapshot is None:
            messagebox.showwarning('修正元なし','まず建築設計を作成してください。')
            return
        instruction = self.prompt.get('1.0','end').strip()
        model = self.model.get().strip()
        provider = self.llm_provider.get()
        engine = self.manager.active_path
        if not instruction or not model or engine is None:
            messagebox.showwarning('入力不足','修正内容を入力し、ローカルAIを選択してください。')
            return
        original = self.design
        pinned_hash = self.scene['geometrySha256']
        def work():
            check = run_bridge(engine, original)
            if check['geometrySha256'] != pinned_hash:
                raise StudioError('現在のエンジンは元の設計を再現できません。変更を中止しました')
            error = ''
            draft = None
            for attempt in range(2):
                prev = draft
                try:
                    draft = ask_llm(provider, model, instruction, error,
                                    previous_design=prev, revision_base=original)
                    scene = run_bridge(engine, draft)
                    return draft, scene, attempt
                except StudioError as exc:
                    error = str(exc)
            raise StudioError('AI修正候補がMAB SOURCE検証に通りませんでした: '+error)
        def done(data):
            revised, scene, retry = data
            self._display_design(revised, scene, provider, model,
                                 'AI修正案 / 施工なし', operation='revision')
            self.view_mode.set('変更差分')
            self._refresh_view()
        self._start('AI修正案の検証＋差分計算（ワールドには未反映）',work,done)

    def restore_previous(self):
        engine=self.manager.active_path
        if engine is None:
            return
        def done(data):
            self.current_snapshot=data
            self.design=data['design']
            self.scene=data['scene']
            self.delta=None
            self.view_mode.set('建築のみ')
            self._refresh_view()
            self._refresh_buttons()
            self.caption.set('過去の設計を新しい履歴として復元しました（ワールドは変更されていません）')
        self._start('真正MABで旧設計を再検証してプレビューだけ戻す',
                    lambda:self.history.restore_previous(engine_commit=self.manager.active_sha,
                          verify=lambda design:run_bridge(engine,design)),done)

    def load_saved_design(self):
        engine=self.manager.active_path
        if engine is None:
            messagebox.showwarning('エンジン未導入','先にB143を導入してください。')
            return
        def work():
            old=self.history.head()
            if old is None:
                raise StudioError('保存された設計履歴はありません')
            scene=run_bridge(engine, old['design'])
            if scene['geometrySha256'] != old['geometrySha256']:
                raise StudioError('保存された設計とMAB出力が一致しません')
            return old,scene
        def done(data):
            old,scene=data
            self.current_snapshot=old
            self.design=old['design']
            self.scene=scene
            self.delta=None
            self._refresh_view()
            self._refresh_buttons()
            self.export_btn.configure(state='normal')
            self.caption.set('保存された設計を真正MABで再検証しました。Minecraftの施工記録ではありません')
        self._start('保存した履歴を真正MABで再検証中',work,done)

    def export_design(self):
        if not self.scene:
            return
        # Existing verified files only: export is app-output, never MakeCode/world writes.
        target=self.manager.root/'outputs'
        self.status.set('保存済み: '+str(target/'latest-design.json')+' / latest-scene.json')
        messagebox.showinfo('保存先','検証済みデータを保存しています。\n'+str(target)+
                            '\n\nこれはMinecraftへ直接インポートするファイルではありません。')


def main():
    if '--smoke' in sys.argv:
        # Explicit release-test command.  No Minecraft connection or world
        # writes; GUI and optional real pinned B143 are checked in isolation.
        from app.smoke import run_smoke
        return run_smoke(Studio, sys.argv[1:])
    Studio().mainloop()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
