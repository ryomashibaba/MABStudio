"""Engine download/pinning, compatibility checks and local LLM transport.

No API here connects to Minecraft or writes world coordinates.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

REPO = "ryomashibaba/MinecraftAIBuilder"
DEFAULT_BRANCH = "feature/b143-universal-llm-building-dsl"
# Verified via GitHub metadata on 2026-11-17; this SHA is the known B143 contract.
BASELINE_SHA = "b0295c2f5012877f57ae218ae791634794de7b52"
HEX40 = re.compile(r"^[a-f0-9]{40}$")
ENGINE_FILE = "source/ai/b143-building-dsl.mjs"
SCHEMA = "minecraftaibuilder-b143-building-dsl-v1"
MAX_ZIP_BYTES = 60 * 1024 * 1024
MAX_UNPACK_BYTES = 260 * 1024 * 1024
MAX_MEMBERS = 5000
MAX_RESPONSE = 1024 * 1024
OLLAMA_BASE = "http://127.0.0.1:11434"
LM_STUDIO_BASE = "http://127.0.0.1:1234"
# Only known loopback endpoints may receive user prompts.  urllib's default
# opener follows HTTP redirects, which could otherwise leak the prompt off-host.
class _RefuseModelRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise StudioError("ローカルAIが外部へのリダイレクトを要求したため通信を拒否しました")


LOCAL_HTTP = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _RefuseModelRedirect()
)
# Exact SHA256 of GitHub B143 source at commit b0295c2... (Git blob 3e257f9f...).
# Source changes require a separately reviewed app release; a GitHub branch move
# is never permission to execute unreviewed third-party JavaScript on a user's PC.
APPROVED_B143_SOURCE_SHA256 = "537aa9e4a2b4e027f16179ef7a306b604ca4d850713d6de23940667ed2348e16"
PINNED_SMOKE_GEOMETRY_SHA256 = "0486ea0ef8a5ea9741742251eec2e55cdd235fb8106068ba6ae4b8508d01403b"


class StudioError(Exception):
    pass


def user_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "MABStudio"


def atomic_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".state-", suffix=".tmp", delete=False) as f:
        tmp = Path(f.name)
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def json_read(path: Path, default: object = None) -> object:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def request_json(url: str, timeout: int = 12) -> dict:
    if not url.startswith(f"https://api.github.com/repos/{REPO}/"):
        raise StudioError("GitHub以外のURLへ接続できません")
    req = urllib.request.Request(url, headers={"User-Agent": "MABStudio/0.1", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        raw = res.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise StudioError("GitHubの応答が大きすぎます")
    return json.loads(raw)


def branch_sha(branch: str = DEFAULT_BRANCH) -> str:
    # Do not allow custom URLs, backtracking, query strings or accidental ref injection.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,120}", branch) or ".." in branch or "//" in branch:
        raise StudioError("ブランチ名が不正です")
    safe = urllib.parse.quote(branch, safe="/")
    data = request_json(f"https://api.github.com/repos/{REPO}/branches/{safe}")
    sha = data.get("commit", {}).get("sha", "")
    if not HEX40.fullmatch(sha):
        raise StudioError("GitHubから正しいコミットSHAを取得できません")
    return sha


def _source_sha256(engine: Path) -> str:
    source = engine / ENGINE_FILE
    if not source.is_file():
        raise StudioError("MAB B143エンジンが存在しません")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != APPROVED_B143_SOURCE_SHA256:
        raise StudioError("B143のコードが安全確認済み版と異なります。更新を保留します")
    return digest


def _sandbox_node_args(node: str, engine: Path, inp: Path, out: Path) -> list[str]:
    # A reviewed B143 source has zero local JS imports: grant only these 3 reads.
    # Node 22+ permission model blocks unexpected fs reads/writes, workers and
    # child processes. This is an additional guard, not a full network sandbox.
    script = bridge_script().resolve()
    return [node, "--permission",
            f"--allow-fs-read={script}",
            f"--allow-fs-read={(engine / ENGINE_FILE).resolve()}",
            f"--allow-fs-read={inp.resolve()}",
            f"--allow-fs-write={out.resolve()}",
            str(script)]


def node_binary() -> str:
    # Windows distribution includes its own node.exe; developers may use PATH.
    places = []
    if getattr(sys, "frozen", False):
        places.append(Path(sys.executable).resolve().parent / "node.exe")
    places.append(Path(__file__).resolve().parent / "node.exe")
    for p in places:
        if p.is_file():
            return str(p)
    node = shutil.which("node")
    if not node:
        raise StudioError("Node.jsが見つかりません。配布版EXEには同梱予定です。")
    return node


def bridge_script() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / "app" / "engine_bridge.mjs"


def run_bridge(engine: Path, design: dict, timeout: int = 60) -> dict:
    """The Node bridge imports the *actual installed MAB module*, never a copy."""
    _source_sha256(engine)
    with tempfile.TemporaryDirectory(prefix="mab-studio-compile-") as d:
        tmp = Path(d)
        inp = tmp / "design.json"
        out = tmp / "scene.json"
        inp.write_text(json.dumps(design, ensure_ascii=False), encoding="utf-8")
        args = _sandbox_node_args(node_binary(), engine, inp, out) + ["compile", str(engine.resolve()), str(inp), str(out)]
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=d)
        except subprocess.TimeoutExpired as e:
            raise StudioError("MAB SOURCE検証が時間切れになりました") from e
        if r.returncode:
            msg = (r.stderr or r.stdout or "MABの検証失敗").strip()[-700:]
            raise StudioError(msg)
        if not out.exists() or out.stat().st_size > 16 * 1024 * 1024:
            raise StudioError("MABの出力がありません、または大きすぎます")
        scene = json_read(out)
        if not isinstance(scene, dict):
            raise StudioError("MABの出力が正しくありません")
        # Independent refusal gate for app-owned actions (not a reimplementation of MAB).
        if (scene.get("designOnly") is not True or scene.get("readiness") != "NOT_PREPARED"
                or scene.get("worldContacts") != 0 or scene.get("worldCoordinates") is not None
                or scene.get("stats", {}).get("constructionApproved") is not False
                or scene.get("BuildPlan", {}).get("operations") != []):
            raise StudioError("施工禁止ゲートに不一致があります。処理を中断しました")
        return scene


def compatibility_check(engine: Path) -> dict:
    """Run a known small design against the downloaded engine before activation."""
    doc = {"schema": SCHEMA, "id": "studio-smoke", "title": "Studio smoke test",
           "brief": "Offline geometry check", "designOnly": True,
           "bounds": {"width": 12, "height": 12, "depth": 12},
           "elements": [{"id": "base", "shape": "box", "mode": "add", "material": "stone",
                         "origin": [2, 1, 2], "size": [8, 3, 8]}]}
    scene = run_bridge(engine, doc)
    if (scene.get("stats", {}).get("voxels") != 192
            or scene.get("geometrySha256") != PINNED_SMOKE_GEOMETRY_SHA256):
        raise StudioError("MABの計算結果が安全確認済み版と異なります")
    return {"sourceSha256": scene["sourceSha256"], "geometrySha256": scene["geometrySha256"],
            "schema": SCHEMA, "voxels": 192, "readOnly": True}


def _safe_extract_zip(zbytes: bytes, target: Path, expected_commit: str | None = None) -> None:
    if len(zbytes) > MAX_ZIP_BYTES:
        raise StudioError("アーカイブの容量が上限を超えています")
    with zipfile.ZipFile(io.BytesIO(zbytes)) as z:
        items = z.infolist()
        if len(items) > MAX_MEMBERS or sum(x.file_size for x in items) > MAX_UNPACK_BYTES:
            raise StudioError("アーカイブの展開容量またはファイル数が上限を超えています")
        files = [x for x in items if not x.is_dir()]
        if not files:
            raise StudioError("空のアーカイブです")
        roots = {x.filename.split("/")[0] for x in files}
        if len(roots) != 1:
            raise StudioError("GitHubアーカイブのルートが異常です")
        root_name = next(iter(roots))
        if expected_commit is not None:
            if not HEX40.fullmatch(expected_commit):
                raise StudioError("検証するコミットが不正です")
            prefix = "MinecraftAIBuilder-"
            suffix = root_name[len(prefix):] if root_name.startswith(prefix) else ""
            if not (7 <= len(suffix) <= 40 and re.fullmatch("[a-f0-9]+", suffix)
                    and expected_commit.startswith(suffix)):
                raise StudioError("ダウンロードしたアーカイブとコミットが一致しません")
        seen = set()
        for member in items:
            # ZIP entries must be portable to Windows, even during Linux tests.
            name = member.filename
            if "\\" in name or name.startswith("/") or name.startswith("//"):
                raise StudioError("ZIPにWindows非互換パスがあります")
            parts = name.rstrip("/").split("/")
            if member.is_dir() and parts == [root_name]:
                target.mkdir(parents=True, exist_ok=True)
                continue
            if (len(parts) < 2 or parts[0] != root_name
                    or any(x in ("", ".", "..") or ":" in x or x[-1:] in (" ", ".")
                           for x in parts)):
                raise StudioError("不正なアーカイブパスです")
            if any(re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", x, re.I)
                   for x in parts[1:]):
                raise StudioError("Windows予約ファイル名は禁止されています")
            path_key = "/".join(parts[1:]).lower()
            if path_key in seen:
                raise StudioError("Windowsで重複するアーカイブパスがあります")
            seen.add(path_key)
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise StudioError("シンボリックリンクは禁止されています")
            relative = PurePosixPath(*parts[1:])
            dest = target.joinpath(*relative.parts)
            if member.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as src, dest.open("xb") as dst:
                    shutil.copyfileobj(src, dst, 128 * 1024)


class EngineManager:
    def __init__(self, root: Path | None = None):
        self.root = root or user_root()
        self.versions = self.root / "engines"
        self.state_path = self.root / "state.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.state = json_read(self.state_path, {})
        if not isinstance(self.state, dict):
            raise StudioError("状態ファイルを読み取れません")

    @property
    def active_sha(self) -> str | None:
        return self.state.get("active")

    @property
    def active_path(self) -> Path | None:
        sha = self.active_sha
        if not isinstance(sha, str) or not HEX40.fullmatch(sha):
            return None
        p = self.versions / sha
        try:
            self._check_integrity(p, sha)
            return p
        except (StudioError, OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _check_integrity(engine: Path, sha: str) -> None:
        evidence = json_read(engine / ".studio-evidence.json")
        if not isinstance(evidence, dict) or evidence.get("commit") != sha:
            raise StudioError("インストール証跡が不正です")
        digest = _source_sha256(engine)
        if digest != evidence.get("engineSha256"):
            raise StudioError("エンジンファイルの改変を検出しました")

    def install(self, sha: str, *, archive: bytes | None = None) -> dict:
        if not HEX40.fullmatch(sha):
            raise StudioError("更新に使うコミットSHAが不正です")
        target = self.versions / sha
        if target.exists():
            self._check_integrity(target, sha)
            evidence = compatibility_check(target)
        else:
            self.versions.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.versions))
            try:
                if archive is None:
                    url = f"https://codeload.github.com/{REPO}/zip/{sha}"
                    req = urllib.request.Request(url, headers={"User-Agent": "MABStudio/0.1"})
                    with urllib.request.urlopen(req, timeout=45) as r:
                        archive = r.read(MAX_ZIP_BYTES + 1)
                if len(archive) > MAX_ZIP_BYTES:
                    raise StudioError("ダウンロード容量の上限を超えました")
                _safe_extract_zip(archive, staging, expected_commit=sha)
                evidence = compatibility_check(staging)
                atomic_json(staging / ".studio-evidence.json", {"commit": sha,
                            "archiveSha256": hashlib.sha256(archive).hexdigest(),
                            "engineSha256": hashlib.sha256((staging / ENGINE_FILE).read_bytes()).hexdigest(),
                            "compatibility": evidence})
                staging.rename(target)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        # The active version changes only AFTER successful compilation & safety checks.
        old = self.active_sha
        new_state = {**self.state, "active": sha, "previous": old if old != sha else self.state.get("previous"),
                     "tracked_branch": self.state.get("tracked_branch", DEFAULT_BRANCH)}
        atomic_json(self.state_path, new_state)
        self.state = new_state
        return evidence

    def rollback(self) -> str:
        prev = self.state.get("previous")
        if not isinstance(prev, str) or not HEX40.fullmatch(prev) or not (self.versions / prev / ENGINE_FILE).is_file():
            raise StudioError("戻せる旧バージョンがありません")
        self._check_integrity(self.versions / prev, prev)
        compatibility_check(self.versions / prev)
        active = self.active_sha
        new_state = {**self.state, "active": prev, "previous": active}
        atomic_json(self.state_path, new_state)
        self.state = new_state
        return prev

    def save_branch(self, branch: str) -> None:
        # Validate without network call.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,120}", branch) or ".." in branch or "//" in branch:
            raise StudioError("ブランチ名が不正です")
        obj = {**self.state, "tracked_branch": branch}
        atomic_json(self.state_path, obj)
        self.state = obj


def parse_llm_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    if len(text) > MAX_RESPONSE:
        raise StudioError("LLMの応答が大きすぎます")
    try:
        d = json.loads(text)
    except json.JSONDecodeError as e:
        raise StudioError("LLMが設計JSONを返しませんでした") from e
    if not isinstance(d, dict):
        raise StudioError("LLMの設計形式が正しくありません")
    return d


SYSTEM_PROMPT = """あなたはMinecraftAIBuilder B143の設計JSON生成器です。
JSONオブジェクトだけを出力してください。前置きやMarkdownは禁止。
必須形式：
{"schema":"minecraftaibuilder-b143-building-dsl-v1","id":"lowercase-id","title":"短い題名","brief":"短い説明","designOnly":true,"bounds":{"width":32,"height":24,"depth":32},"elements":[...]}
各elementsは例えば
{"id":"wall","shape":"box","mode":"add","material":"stone","origin":[1,1,1],"size":[10,5,10],"shell":1}
{"id":"opening","shape":"box","mode":"cut","origin":[4,1,1],"size":[3,4,2]}
を使えます。形状は box / ellipsoid(center,radii) / cylinder(origin,axis,radius,length)。
materialは stone, concrete, metal, glass, wood, glow, accent の7種。
整数、各要素がbounds内、elements最大160、縦軸はy。全体積50万以下。概念建築のみ。
構造の美観を優先し、外装・屋根・窓・入口・内装を可能な範囲で工夫。
Minecraftワールド座標、実行可能なコマンド、プログラム、URLを出力しない。
"""


def llm_models(provider: str, timeout: int = 5) -> list[str]:
    if provider not in ("Ollama", "LM Studio"):
        raise StudioError("ローカルLLMの種類が不正です")
    url = OLLAMA_BASE + "/api/tags" if provider == "Ollama" else LM_STUDIO_BASE + "/v1/models"
    with LOCAL_HTTP.open(url, timeout=timeout) as res:
        data = json.loads(res.read(MAX_RESPONSE))
    if provider == "Ollama":
        return [x["name"] for x in data.get("models", []) if isinstance(x.get("name"), str)]
    return [x["id"] for x in data.get("data", []) if isinstance(x.get("id"), str)]


def ask_llm(provider: str, model: str, user_prompt: str, error: str = "",
            previous_design: dict | None = None, timeout: int = 180,
            revision_base: dict | None = None) -> dict:
    if provider not in ("Ollama", "LM Studio"):
        raise StudioError("ローカルLLMの接続先が不正です")
    if not model or len(model) > 180 or not user_prompt.strip() or len(user_prompt) > 2000:
        raise StudioError("モデル名または指示文を確認してください")
    task = user_prompt
    if revision_base is not None:
        if not isinstance(revision_base, dict) or revision_base.get('schema') != SCHEMA:
            raise StudioError('修正元のMAB B143設計が正しくありません')
        prior = json.dumps(revision_base, ensure_ascii=False, separators=(',', ':'))
        if len(prior) > 64000:
            raise StudioError('修正元の設計が長すぎます。全文をAIへ渡せません')
        task = ('【既存の確定設計を修正】以下のB143 JSONを保ち、変更の必要な部分だけ修正し、' 
                '省略のないB143 JSON全体を返してください。Minecraftに実行しないこと。\n' 
                '既存設計: ' + prior + '\n修正指示: ' + user_prompt)
    if error:
        task += "\n\n前回のSOURCE検証エラー: " + error[-500:]
        if previous_design is not None:
            # Supply the exact invalid design, not only an error string.
            # The model can correct it without regenerating unrelated details.
            prior = json.dumps(previous_design, ensure_ascii=False, separators=(",", ":"))
            task += "\n修正する前回の設計JSON: " + prior[:12000]
        task += "\n元の建築意図を保ち、エラーだけ直した完全なJSONを返してください。"
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": task}]
    if provider == "Ollama":
        url = OLLAMA_BASE + "/api/chat"
        payload = {"model": model, "messages": messages, "stream": False, "format": "json",
                   "options": {"temperature": 0.3, "num_predict": 4500}}
    else:
        url = LM_STUDIO_BASE + "/v1/chat/completions"
        payload = {"model": model, "messages": messages, "temperature": 0.3, "max_tokens": 4500, "stream": False}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data, headers={"Content-Type": "application/json", "User-Agent": "MABStudio/0.1"})
    try:
        with LOCAL_HTTP.open(req, timeout=timeout) as r:
            answer = r.read(MAX_RESPONSE + 1)
    except (OSError, TimeoutError) as e:
        raise StudioError(f"{provider} に接続できません: {e}") from e
    if len(answer) > MAX_RESPONSE:
        raise StudioError("LLMの応答が大きすぎます")
    try:
        data = json.loads(answer)
    except json.JSONDecodeError as exc:
        raise StudioError("ローカルLLMサーバーから不正なJSON応答を受け取りました") from exc
    content = (data.get("message", {}).get("content", "") if provider == "Ollama"
               else data.get("choices", [{}])[0].get("message", {}).get("content", ""))
    if not isinstance(content, str):
        raise StudioError("LLMの出力が文字列ではありません")
    return parse_llm_json(content)
