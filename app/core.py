"""Engine download/pinning, compatibility checks and local LLM transport.

No API here connects to Minecraft or writes world coordinates.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import urllib.error
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
MAX_PINNED_MODULE_BYTES = 256 * 1024
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
    if getattr(sys, "frozen", False):
        # A released EXE must NEVER silently rely on a developer's PATH Node.
        # Otherwise CI can pass while the distributed zip is nonfunctional.
        bundled = Path(sys.executable).resolve().parent / "node.exe"
        if not bundled.is_file():
            raise StudioError("配布EXEのnode.exeがありません。dist\\MABStudioフォルダを丸ごと展開してください。")
        return str(bundled)
    local = Path(__file__).resolve().parent / "node.exe"
    if local.is_file():
        return str(local)
    node = shutil.which("node")
    if not node:
        raise StudioError("Node.js 22以降が見つかりません。開発用ソース実行にはNode.jsが必要です。")
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


def _download_reviewed_b143(sha: str) -> bytes:
    """Fetch only the one independently approved B143 JS file, not an entire repo.

    The commit AND content hash are pinned. This never tracks a moving branch or
    silently authorizes a newer MAB engine. Offline use works once installed.
    """
    if sha != BASELINE_SHA:
        raise StudioError("新しいMABコミットはまだStudioの互換性・安全審査対象外です。導入を保留します")
    url = (f"https://raw.githubusercontent.com/{REPO}/{sha}/"
           f"{ENGINE_FILE}")
    req = urllib.request.Request(url, headers={"User-Agent": "MABStudio/0.3"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            source = res.read(MAX_PINNED_MODULE_BYTES + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise StudioError("固定B143ソースの取得に失敗しました。接続後に再試行してください") from exc
    if not source or len(source) > MAX_PINNED_MODULE_BYTES:
        raise StudioError("固定B143モジュールのサイズが不正です")
    if hashlib.sha256(source).hexdigest() != APPROVED_B143_SOURCE_SHA256:
        raise StudioError("取得したB143モジュールのSHA-256が承認済み原本と一致しません")
    return source


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
                    # The reviewed B143 module has no imports or other source
                    # dependencies. Downloading the giant repository archive is
                    # unnecessary for the normal first-run experience.
                    source = _download_reviewed_b143(sha)
                    dest = staging / ENGINE_FILE
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(source)
                    provenance = {"installMethod": "PINNED_SINGLE_MODULE",
                                  "moduleDownloadSha256": hashlib.sha256(source).hexdigest()}
                else:
                    if len(archive) > MAX_ZIP_BYTES:
                        raise StudioError("ダウンロード容量の上限を超えました")
                    _safe_extract_zip(archive, staging, expected_commit=sha)
                    provenance = {"installMethod": "EXPLICIT_ARCHIVE",
                                  "archiveSha256": hashlib.sha256(archive).hexdigest()}
                evidence = compatibility_check(staging)
                atomic_json(staging / ".studio-evidence.json", {"commit": sha,
                            **provenance,
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
整数、各要素がbounds内、SOURCE仕様上はelements最大160、今回のAI生成は最大32。縦軸はy。全体積50万以下。概念建築のみ。
構造の美観を優先し、外装・屋根・窓・入口・内装を可能な範囲で工夫。
設計JSONのelementsは最大32個。箱やシェル・切り抜き・円柱を利用して、窓の多い4階建てでも独創的な外装と機能を簡潔に表現。
要素IDと説明文は短く、余分な説明・推論・空白を出力せず、完全なJSONだけで必ず終了する。
Minecraftワールド座標、実行可能なコマンド、プログラム、URLを出力しない。
"""


# Schema is a transport/output constraint, NOT a replacement for the pinned B143
# validateDesign/compileDesign/verifyCompiled authority. Some local models cannot
# generate grammar-constrained JSON; the API wrapper handles explicit 400/422
# incompatibility below without claiming semantic or architectural success.
B143_JSON_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["schema", "id", "title", "brief", "designOnly", "bounds", "elements"],
    "properties": {
        "schema": {"const": SCHEMA},
        "id": {"type": "string", "pattern": "^[a-z][a-z0-9-]{2,47}$"},
        "title": {"type": "string", "minLength": 1, "maxLength": 100},
        "brief": {"type": "string", "minLength": 1, "maxLength": 500},
        "designOnly": {"const": True},
        "bounds": {
            "type": "object", "additionalProperties": False,
            "required": ["width", "height", "depth"],
            "properties": {
                "width": {"type": "integer", "minimum": 8, "maximum": 128},
                "height": {"type": "integer", "minimum": 8, "maximum": 96},
                "depth": {"type": "integer", "minimum": 8, "maximum": 128},
            },
        },
        "elements": {
            "type": "array", "minItems": 1, "maxItems": 160,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "shape", "mode"],
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-zA-Z][a-zA-Z0-9_-]{0,39}$"},
                    "shape": {"type": "string", "enum": ["box", "ellipsoid", "cylinder"]},
                    "mode": {"type": "string", "enum": ["add", "cut"]},
                    "material": {"type": "string", "enum": ["stone", "concrete", "metal", "glass", "wood", "glow", "accent"]},
                    "origin": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 128}, "minItems": 3, "maxItems": 3},
                    "size": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 128}, "minItems": 3, "maxItems": 3},
                    "center": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 128}, "minItems": 3, "maxItems": 3},
                    "radii": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 64}, "minItems": 3, "maxItems": 3},
                    "axis": {"type": "string", "enum": ["x", "y", "z"]},
                    "radius": {"type": "integer", "minimum": 1, "maximum": 64},
                    "length": {"type": "integer", "minimum": 1, "maximum": 128},
                    "shell": {"type": "integer", "minimum": 0, "maximum": 4},
                },
            },
        },
    },
}


class ModelTransportError(StudioError):
    """A server/transport failure; do not repeat expensive model inference."""


class ModelOutputLimitError(StudioError):
    """The model ended before its full JSON. Never retry with the same limits."""


# This is an OUTPUT ceiling, not a guarantee of capacity. LM Studio can have a
# smaller loaded context window. In that case we reduce the output allowance
# without silently changing or reloading the user's model (which costs VRAM).
DEFAULT_OUTPUT_TOKENS = 6400
MAX_OUTPUT_TOKENS = 8000
# A complete B143 source is still verified by the pinned MAB engine. The output
# grammar only bounds the FIRST proposal so a normal building fits in one reply.
MAX_MODEL_ELEMENTS = 32
MODEL_GENERATION_SCHEMA = {
    **B143_JSON_SCHEMA,
    "properties": {**B143_JSON_SCHEMA["properties"],
                   "elements": {**B143_JSON_SCHEMA["properties"]["elements"],
                                "maxItems": MAX_MODEL_ELEMENTS}},
}
# Long local GGUF inferences can need several minutes; while streaming, these
# limits apply to idle reads and the complete request, not the total first-byte
# to last-byte HTTP buffering delay of a non-streaming reply.
MODEL_STREAM_IDLE_TIMEOUT = 300
MODEL_STREAM_TOTAL_TIMEOUT = 900


def _loaded_lm_studio_context(model: str, timeout: float = 2.5) -> int | None:
    """Read the selected *already loaded* LM Studio model's context, if exposed.

    The optional /api/v1/models endpoint must never load a model, change its
    settings, or become a hard dependency for older OpenAI-compatible servers.
    """
    try:
        req = urllib.request.Request(LM_STUDIO_BASE + '/api/v1/models',
                                     headers={'User-Agent': 'MABStudio/0.3'})
        with LOCAL_HTTP.open(req, timeout=timeout) as res:
            raw = res.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            return None
        data = json.loads(raw)
        entries = data.get('models') if isinstance(data, dict) else None
        if not isinstance(entries, list):
            return None
        matches = []
        for item in entries[:150]:
            if not isinstance(item, dict):
                continue
            loaded = item.get('loaded_instances')
            if not isinstance(loaded, list):
                continue
            for instance in loaded[:20]:
                if not isinstance(instance, dict):
                    continue
                if instance.get('id') != model and item.get('key') != model:
                    continue
                config = instance.get('config')
                length = config.get('context_length') if isinstance(config, dict) else None
                if type(length) is int and 1024 <= length <= 1048576:
                    matches.append(length)
        # If an alias matches several instances, use the smaller window.
        return min(matches) if matches else None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError, TypeError):
        return None


def _output_budget(provider: str, model: str, messages: list[dict]) -> tuple[int, int | None]:
    context = _loaded_lm_studio_context(model) if provider == 'LM Studio' else None
    if context is None:
        return DEFAULT_OUTPUT_TOKENS, None
    # Conservative headroom for instructions, schema, generation bookkeeping and
    # tokenization differences. This is only an estimate, not a tokenizer.
    request_chars = len(json.dumps({'messages': messages,
                                   'schema': MODEL_GENERATION_SCHEMA}, ensure_ascii=False))
    reserve = max(1800, (request_chars + 1) // 2 + 480)
    remaining = context - reserve
    if remaining < 800:
        raise ModelOutputLimitError(
            f'LM Studioのロード済みコンテキスト長{context}トークンでは、'
            'この設計指示とSOURCE形式に必要な余裕がありません。'
            'モデルのコンテキスト設定を確認してください（Studioは変更しません）。')
    return min(MAX_OUTPUT_TOKENS, remaining), context


def _truncated_error(provider: str, reply: dict, budget: int,
                     context: int | None) -> ModelOutputLimitError:
    usage = reply.get('usage')
    consumed = usage.get('completion_tokens') if isinstance(usage, dict) else None
    count = f'、生成済み{consumed}' if type(consumed) is int and consumed >= 0 else ''
    ctx = f'、ロード済み文脈{context}' if context is not None else ''
    return ModelOutputLimitError(
        f'{provider}の設計JSON生成がトークン上限で途中終了しました'
        f'（出力上限{budget}{count}{ctx}）。MAB SOURCEはまだ検証していません。'
        '同じ条件での無駄な再推論は中止しました。'
        'LM Studioではモデルのコンテキスト長・生成上限を確認してください。'
        'コンテキスト長を増やすとRAM/VRAM使用量も増える可能性があります。')


def _read_lm_studio_stream(res, started: float, on_progress=None) -> dict:
    """Read a single OpenAI-compatible SSE response, without repeating inference.

    Only the final assistant content is used. Reasoning tokens are never
    mistaken for JSON; invalid/unfinished events and timeout fail closed.
    """
    content = []
    chars = 0
    finish = None
    usage = None
    saw_done = False
    last_notice = started
    while True:
        if time.monotonic() - started > MODEL_STREAM_TOTAL_TIMEOUT:
            raise ModelTransportError('ローカルAI生成が安全な総時間上限を超えました（15分）。追加推論せず停止しました')
        line = res.readline(MAX_RESPONSE + 1)
        if not line:
            break
        if len(line) > MAX_RESPONSE:
            raise ModelTransportError('ローカルAIのストリーム1行が大きすぎます')
        if not line.startswith(b'data:'):
            continue
        item = line[5:].strip()
        if item == b'[DONE]':
            saw_done = True
            break
        try:
            packet = json.loads(item)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ModelTransportError('ローカルAIのストリーム応答が壊れています') from exc
        if not isinstance(packet, dict):
            raise ModelTransportError('ローカルAIのストリーム形式が不正です')
        if 'error' in packet:
            raise ModelTransportError('ローカルAIが生成中にエラーを返しました。LM Studioのログを確認してください')
        if isinstance(packet.get('usage'), dict):
            usage = packet['usage']
        choices = packet.get('choices')
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if not isinstance(choice, dict):
                raise ModelTransportError('ローカルAIの候補形式が不正です')
            delta = choice.get('delta')
            part = delta.get('content') if isinstance(delta, dict) else None
            if part is not None and not isinstance(part, str):
                raise ModelTransportError('ローカルAIの本文形式が不正です')
            if part:
                content.append(part)
                chars += len(part)
                if chars > MAX_RESPONSE:
                    raise ModelTransportError('ローカルAIの出力が大きすぎます')
            if choice.get('finish_reason') is not None:
                finish = choice['finish_reason']
        now = time.monotonic()
        if on_progress is not None and now - last_notice >= 1.0:
            on_progress('ローカルAIが生成中：JSON本文 '+str(chars)+'文字（SOURCE未検証）')
            last_notice = now
    if not saw_done or finish is None:
        raise ModelTransportError('ローカルAIのストリームが完了前に切断されました。追加推論はしません')
    return {'choices': [{'message': {'content': ''.join(content)},
                         'finish_reason': finish}], 'usage': usage or {}}


def _model_response(url: str, payload: dict, timeout: int, on_progress=None) -> dict:
    req = urllib.request.Request(url,
        json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'User-Agent': 'MABStudio/0.3'})
    try:
        # The streaming transport is only used for LM Studio. Fake/test servers
        # may still return one JSON body; that is handled without another POST.
        with LOCAL_HTTP.open(req, timeout=(MODEL_STREAM_IDLE_TIMEOUT if payload.get('stream') else timeout)) as res:
            if payload.get('stream') and 'text/event-stream' in res.headers.get('Content-Type', '').lower():
                return _read_lm_studio_stream(res, time.monotonic(), on_progress)
            raw = res.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError:
        raise
    except (OSError, TimeoutError) as exc:
        raise ModelTransportError('ローカルAIとの通信がタイムアウト/切断されました。LM Studioの推論速度・VRAMとサーバー状態を確認してください（追加推論なし）: ' + str(exc)[:150]) from exc
    if len(raw) > MAX_RESPONSE:
        raise StudioError('LLMの応答が大きすぎます')
    try:
        reply = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StudioError('ローカルLLMサーバーが不正なJSON応答を返しました') from exc
    if not isinstance(reply, dict):
        raise StudioError('ローカルLLMサーバーの応答形式が不正です')
    return reply


def _local_model_json(path: str, *, method: str = 'GET', payload: dict | None = None,
                      timeout: float = 10.0, provider: str = 'ローカルAI') -> dict:
    """Small, bounded localhost-only model-management requests; never follow redirects."""
    base = LM_STUDIO_BASE if provider == 'LM Studio' else OLLAMA_BASE
    if provider not in ('LM Studio', 'Ollama') or not path.startswith('/') or '//' in path:
        raise StudioError('モデル管理の接続先が不正です')
    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(base + path, data=body, method=method,
                                 headers={'Content-Type': 'application/json',
                                          'User-Agent': 'MABStudio/0.3'})
    try:
        with LOCAL_HTTP.open(req, timeout=timeout) as res:
            raw = res.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise StudioError('モデル管理の応答が大きすぎます')
        result = json.loads(raw)
    except urllib.error.HTTPError as exc:
        if provider == 'LM Studio' and exc.code in (404, 405, 501):
            raise StudioError('LM Studioのモデル管理APIが使えません。0.4.0以降か確認してください。') from exc
        raise StudioError(f'{provider}のモデル操作がHTTP {exc.code}で拒否されました') from exc
    except (urllib.error.URLError, OSError, TimeoutError, ValueError, UnicodeError) as exc:
        raise StudioError(f'{provider}のローカルサーバーとの通信に失敗しました: {str(exc)[:100]}') from exc
    if not isinstance(result, dict):
        raise StudioError('モデル管理の応答形式が不正です')
    return result


def _lm_instance_for_model(models: dict, model: str) -> str:
    """Select exactly one loaded instance by key or instance ID; never unload all."""
    items = models.get('models')
    if not isinstance(items, list):
        raise StudioError('LM Studioのロード情報を取得できません')
    selected = []
    for entry in items[:200]:
        if not isinstance(entry, dict):
            continue
        instances = entry.get('loaded_instances', [])
        if not isinstance(instances, list):
            raise StudioError('LM Studioのロード情報が不正です')
        for ins in instances[:100]:
            if not isinstance(ins, dict):
                continue
            instance_id = ins.get('id')
            if isinstance(instance_id, str) and (instance_id == model or entry.get('key') == model):
                selected.append(instance_id)
    selected = list(dict.fromkeys(selected))
    if not selected:
        raise StudioError('選択モデルは現在ロードされていません。アンロードは行いません')
    if len(selected) != 1:
        raise StudioError('同じモデルに複数のロード状態があります。誤終了防止のため操作を中止しました')
    return selected[0]


def _ollama_is_running(result: dict, model: str) -> bool:
    items = result.get('models')
    if not isinstance(items, list):
        raise StudioError('Ollamaの実行中モデル一覧を取得できません')
    return any(isinstance(item, dict) and
               (item.get('name') == model or item.get('model') == model)
               for item in items)


def unload_local_model(provider: str, model: str) -> dict:
    """Explicitly unload only a currently running selected model, with no inference.

    Does not unload all, stop servers, change memory settings or touch Minecraft.
    Confirmation/verification is local and does not re-trigger an inference.
    """
    if provider not in ('Ollama', 'LM Studio'):
        raise StudioError('モデルの提供元が不正です')
    if not isinstance(model, str) or not model.strip() or len(model) > 180 or any(ord(c) < 32 for c in model):
        raise StudioError('アンロードするモデル名を選択してください')
    model = model.strip()
    if provider == 'LM Studio':
        before = _local_model_json('/api/v1/models', provider=provider)
        instance_id = _lm_instance_for_model(before, model)
        reply = _local_model_json('/api/v1/models/unload', method='POST',
                                   payload={'instance_id': instance_id},
                                   provider=provider)
        if reply.get('instance_id') != instance_id:
            raise StudioError('LM Studioの応答とアンロード対象が一致しません。状態を確認してください')
        after = _local_model_json('/api/v1/models', provider=provider)
        try:
            _lm_instance_for_model(after, model)
        except StudioError as exc:
            if 'ロードされていません' in str(exc):
                return {'provider': provider, 'model': model, 'instance_id': instance_id,
                        'unloaded': True, 'worldWrites': 0}
            raise
        raise StudioError('アンロード後も対象モデルがロード中と報告されました。LM Studioを確認してください')
    before = _local_model_json('/api/ps', provider=provider)
    if not _ollama_is_running(before, model):
        raise StudioError('選択モデルは現在ロードされていません。アンロードは行いません')
    reply = _local_model_json('/api/generate', method='POST',
                              payload={'model': model, 'keep_alive': 0, 'stream': False},
                              provider=provider, timeout=15.0)
    if reply.get('model') != model or reply.get('done') is not True or reply.get('done_reason') != 'unload':
        raise StudioError('Ollamaからアンロード完了を確認できません。実行中モデルを確認してください')
    after = _local_model_json('/api/ps', provider=provider)
    if _ollama_is_running(after, model):
        raise StudioError('Ollamaが対象モデルをまだ実行中と報告しています')
    return {'provider': provider, 'model': model, 'unloaded': True, 'worldWrites': 0}


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
            revision_base: dict | None = None, on_progress=None) -> dict:
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
    # A compact FIRST proposal must never shrink a previously approved design.
    # Revision candidates can preserve all source elements plus bounded additions;
    # B143 remains the only source/voxel validation authority.
    max_elements = MAX_MODEL_ELEMENTS
    system_prompt = SYSTEM_PROMPT
    if revision_base is not None:
        existing = revision_base.get('elements')
        if not isinstance(existing, list) or not 1 <= len(existing) <= 160:
            raise StudioError('修正元の要素数がB143の範囲外です')
        max_elements = min(160, max(MAX_MODEL_ELEMENTS, len(existing) + 8))
        system_prompt = SYSTEM_PROMPT.replace('今回のAI生成は最大32',
                                              f'今回の修正案は最大{max_elements}').replace(
                                                  '設計JSONのelementsは最大32個',
                                                  f'設計JSONのelementsは最大{max_elements}個')
    generation_schema = MODEL_GENERATION_SCHEMA if max_elements == MAX_MODEL_ELEMENTS else {
        **MODEL_GENERATION_SCHEMA,
        'properties': {**MODEL_GENERATION_SCHEMA['properties'],
                       'elements': {**MODEL_GENERATION_SCHEMA['properties']['elements'],
                                    'maxItems': max_elements}},
    }
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": task}]
    output_budget, context_length = _output_budget(provider, model, messages)
    if provider == 'Ollama':
        url = OLLAMA_BASE + '/api/chat'
        payload = {'model': model, 'messages': messages, 'stream': False,
                   'format': generation_schema,
                   'options': {'temperature': 0.3, 'num_predict': output_budget}}
        fallback = {**payload, 'format': 'json'}
    else:
        url = LM_STUDIO_BASE + '/v1/chat/completions'
        payload = {'model': model, 'messages': messages, 'temperature': 0.3,
                   'max_tokens': output_budget, 'stream': True,
                   'response_format': {
                       'type': 'json_schema',
                       'json_schema': {'name': 'mab_b143_design', 'schema': generation_schema},
                   }}
        if re.search(r'qwen[.\/_-]?3[.]?8', model, re.I):
            payload['chat_template_kwargs'] = {'enable_thinking': False}
        fallback = {**payload, 'response_format': {'type': 'json_object'}}
    try:
        reply = _model_response(url, payload, timeout, on_progress)
    except urllib.error.HTTPError as exc:
        # HTTP 400 can also mean an unknown model, insufficient resources, or
        # invalid prompt. Never double-infer just because the status is 400.
        try:
            message = exc.read(4096).decode('utf-8', 'replace').lower()
        except (OSError, UnicodeError):
            message = ''
        structured_rejected = exc.code in (400, 422) and any(
            token in message for token in ('response_format', 'json_schema',
                                            'structured output', 'unsupported format',
                                            'invalid format', 'grammar'))
        if not structured_rejected:
            raise ModelTransportError(f'{provider}からHTTP {exc.code}を受信しました。モデル/サーバーを確認してください') from exc
        # Fall back only if the server actually identifies schema/grammar
        # incompatibility; never repeat a failed inference for unrelated 400s.
        try:
            reply = _model_response(url, fallback, timeout, on_progress)
        except urllib.error.HTTPError as fallback_error:
            raise ModelTransportError(f'{provider}がJSON出力を受け付けません (HTTP {fallback_error.code})') from fallback_error
    if provider == 'Ollama':
        if reply.get('done_reason') == 'length':
            raise _truncated_error(provider, reply, output_budget, context_length)
        content = reply.get('message', {}).get('content', '')
    else:
        choices = reply.get('choices')
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise StudioError('LM Studioの回答候補がありません')
        if choices[0].get('finish_reason') == 'length':
            raise _truncated_error(provider, reply, output_budget, context_length)
        content = choices[0].get('message', {}).get('content', '')
    if not isinstance(content, str) or not content.strip():
        raise StudioError('AIから設計JSONの本文を受け取れませんでした。モデルのJSON出力対応を確認してください')
    return parse_llm_json(content)
