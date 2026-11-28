"""Launch the pinned V11 Studio-new runner and require its settled receipt.

This module never calls Studio's legacy direct model API. It prepares the exact
V11 request without HTTP, then delegates execution, monitoring, locking, and
receipt production to the existing V11 runner and PRIVATE binding.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

V11_HEAD = 'ca5053aa82e03e360487745a5ed98aa289264a41'
V11_BRANCH = 'research/r125-v6-readonly-facade-diagnostic-20261111'
MODEL = 'qwen3:14b'
MODEL_DIGEST = 'bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8'
RECEIPT_SCHEMA = 'r125-studio-owned-result-v1'


class V11GenerationError(RuntimeError):
    pass


def default_v11_repo() -> Path:
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        raise V11GenerationError('LOCALAPPDATA_REQUIRED')
    return Path(local) / 'MinecraftAIBuilder' / 'V11StudioExecution'


def default_private_module() -> Path:
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        raise V11GenerationError('LOCALAPPDATA_REQUIRED')
    return (Path(local) / 'MinecraftAIBuilder' / 'PrivateModelStudies' /
            'R125_Owned_Calibration_V11_ec57454e_20261121' /
            'studio-v40.1-binding.mjs')


def default_strata_repo() -> Path:
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        raise V11GenerationError('LOCALAPPDATA_REQUIRED')
    return Path(local) / 'StrataP' / 'SharedLocks-77214370'


def private_run_parent() -> Path:
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        raise V11GenerationError('LOCALAPPDATA_REQUIRED')
    root = Path(local) / 'MinecraftAIBuilder' / 'PrivateModelStudies'
    if root.is_symlink():
        raise V11GenerationError('PRIVATE_RUN_ROOT_SYMLINK')
    parent = root / 'Studio-V40.1-Runs'
    if parent.is_symlink():
        raise V11GenerationError('PRIVATE_RUN_PARENT_SYMLINK')
    parent.mkdir(parents=True, exist_ok=True)
    if parent.resolve(strict=True).parent != root.resolve(strict=True):
        raise V11GenerationError('PRIVATE_RUN_PARENT_SCOPE')
    return parent


def _run_json(argv: list[str], *, env: dict[str, str], cwd: Path) -> tuple[int, dict]:
    completed = subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True,
                               text=True, encoding='utf-8', errors='replace',
                               check=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    try:
        value = json.loads(lines[-1]) if lines else None
    except (ValueError, TypeError):
        value = None
    if not isinstance(value, dict):
        detail = (completed.stderr or completed.stdout or 'NO_JSON_RECEIPT').strip()[-500:]
        raise V11GenerationError('V11_RUNNER_OUTPUT_INVALID: ' + detail)
    return completed.returncode, value


def _child_env(*, private_module: Path, strata_repo: Path) -> dict[str, str]:
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        raise V11GenerationError('LOCALAPPDATA_REQUIRED')
    python = Path(local) / 'Programs' / 'Python' / 'Python313' / 'python.exe'
    ollama = Path(local) / 'Programs' / 'Ollama' / 'ollama.exe'
    for label, path in (('PYTHON', python), ('OLLAMA', ollama),
                        ('PRIVATE_BINDING', private_module), ('STRATA_SHAREDLOCK', strata_repo)):
        if not path.exists():
            raise V11GenerationError(label + '_RUNTIME_MISSING')
    if not python.is_file() or not ollama.is_file() or not private_module.is_file() or not strata_repo.is_dir():
        raise V11GenerationError('RUNTIME_PATH_TYPE_INVALID')
    env = os.environ.copy()
    env.update({
        'MAB_HEAD': V11_HEAD,
        'MAB_PYTHON_EXE': str(python.resolve()),
        'MAB_OLLAMA_EXE': str(ollama.resolve()),
        'MAB_V7_PRIVATE_HOST_MODULE': str(private_module.resolve()),
        'MAB_STRATA_SHAREDLOCK_REPO': str(strata_repo.resolve()),
    })
    return env


def prepare_new_request(*, node_exe: str, prompt: str, model: str = MODEL,
                        repo: Path | None = None, private_module: Path | None = None,
                        strata_repo: Path | None = None) -> tuple[dict, Path, dict[str, str]]:
    """Capture request bytes through V11's POST-free preparation entry."""
    repo = Path(repo or default_v11_repo())
    if repo.is_symlink():
        raise V11GenerationError('V11_REPO_SYMLINK')
    repo = repo.resolve(strict=True)
    try:
        head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'],
                              capture_output=True, text=True, check=True, timeout=8).stdout.strip()
        branch = subprocess.run(['git', '-C', str(repo), 'branch', '--show-current'],
                                capture_output=True, text=True, check=True, timeout=8).stdout.strip()
        dirty = subprocess.run(['git', '-C', str(repo), 'status', '--porcelain=v1',
                                '--untracked-files=normal'], capture_output=True,
                               text=True, check=True, timeout=8).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise V11GenerationError('V11_CHECKOUT_PREFLIGHT_FAILED') from exc
    if head != V11_HEAD or branch != V11_BRANCH or dirty:
        raise V11GenerationError('V11_CHECKOUT_NOT_PINNED_CLEAN')
    private_module = Path(private_module or default_private_module()).resolve(strict=True)
    strata_repo = Path(strata_repo or default_strata_repo()).resolve(strict=True)
    env = _child_env(private_module=private_module, strata_repo=strata_repo)
    env['MAB_HEAD'] = V11_HEAD
    out_dir = private_run_parent() / (uuid.uuid4().hex + '-new')
    if out_dir.exists():
        raise V11GenerationError('PRIVATE_RUN_DIRECTORY_ALREADY_EXISTS')
    script = repo / 'scripts' / 'r125-local-architecture-plan-owned.mjs'
    if not script.is_file():
        raise V11GenerationError('V11_STUDIO_RUNNER_MISSING')
    code, prepared = _run_json([node_exe, str(script), '--prepare-studio-new',
                                '--model', model, '--prompt', prompt,
                                '--out', str(out_dir)], env=env, cwd=repo)
    brief_sha = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
    if (code != 0 or prepared.get('schema') != 'r125-studio-new-request-v1' or
            prepared.get('mode') != 'new' or prepared.get('headSha') != V11_HEAD or
            prepared.get('model') != model or prepared.get('briefSha256') != brief_sha or
            prepared.get('modelPostCount') != 0 or
            not _is_sha(prepared.get('requestSha256'))):
        raise V11GenerationError('V11_POST_FREE_PREPARATION_FAILED: ' +
                                 str(prepared.get('reason') or 'REQUEST_CONTRACT_MISMATCH'))
    return prepared, out_dir, env


def _studio_new_approval(request: dict) -> str:
    """Bind the user's Generate action to this exact, already prepared request.

    The phrase is an ephemeral PRIVATE-verifier input, not a reusable token.
    The verifier independently checks the on-wire body, calibration proof,
    runtime identity, token budget, output format, and one-shot reservation.
    """
    if (request.get('schema') != 'r125-studio-new-request-v1' or
            request.get('mode') != 'new' or request.get('headSha') != V11_HEAD or
            request.get('model') != MODEL or request.get('endpoint') !=
            'http://127.0.0.1:11435/v1/chat/completions' or
            request.get('maxOutputTokens') != 4096 or
            request.get('modelPostCount') != 0 or
            not _is_sha(request.get('requestSha256')) or
            not _is_sha(request.get('briefSha256'))):
        raise V11GenerationError('STUDIO_NEW_APPROVAL_REQUEST_MISMATCH')
    return (f'APPROVE ONE R125 QWEN3:14B STUDIO NEW POST '
            f"{request['requestSha256']} ON HEAD {request['headSha']}")


def _is_sha(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 64 and
            all(char in '0123456789abcdef' for char in value))


def validate_source_ready_receipt(receipt: dict, *, request: dict,
                                  out_dir: Path, bounded: bool = False) -> Path:
    """Accept only a completed, unloaded, single-post V11 receipt."""
    expected = {
        'schema': RECEIPT_SCHEMA, 'mode': 'new', 'status': 'SOURCE_READY_REVIEW_ONLY',
        'headSha': V11_HEAD, 'model': request.get('model'),
        'modelDigest': MODEL_DIGEST,
        'briefSha256': request.get('briefSha256'),
        'requestSha256': request.get('requestSha256'),
        'modelPostCount': 1, 'automaticRetries': 0,
        'loadedModelObserved': True, 'unloadConfirmed': True,
        'ownedServiceStopConfirmed': True, 'monitorStopConfirmed': True,
        'projectLockReleased': True, 'sourceVerified': True,
        'worldContacts': 0, 'worldWrites': 0,
        'constructionApproved': False, 'executionAllowed': False,
        'artStatus': 'NOT_REVIEWED',
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise V11GenerationError('V11_RECEIPT_GATE: ' + key)
    if bounded:
        bounded_expected = {
            'resourceMode': 'BOUNDED_REAL_REQUEST_TRIAL',
            'resourceSafetyCertified': False,
            'inferenceMemoryPeakCalibrated': False,
        }
        for key, value in bounded_expected.items():
            if receipt.get(key) != value:
                raise V11GenerationError('V11_BOUNDED_RECEIPT_GATE: ' + key)
        if not _is_sha(receipt.get('resourceEvidenceSha256')):
            raise V11GenerationError('V11_BOUNDED_EVIDENCE_SHA_INVALID')
    if not _is_sha(receipt.get('sourceSha256')) or not _is_sha(receipt.get('geometrySha256')):
        raise V11GenerationError('V11_RECEIPT_SHA_INVALID')
    reported_path = Path(receipt.get('runPath', ''))
    expected_raw = Path(out_dir)
    if reported_path.is_symlink() or expected_raw.is_symlink():
        raise V11GenerationError('V11_RECEIPT_PATH_SYMLINK')
    run_path = reported_path.resolve(strict=True)
    expected_path = expected_raw.resolve(strict=True)
    if run_path != expected_path:
        raise V11GenerationError('V11_RECEIPT_PATH_MISMATCH')
    return run_path


def run_new_generation(*, node_exe: str, prompt: str, model: str = MODEL,
                       repo: Path | None = None, private_module: Path | None = None,
                       strata_repo: Path | None = None,
                       bounded: bool = False,
                       cancel_event=None, on_execution_start=None) -> tuple[dict, Path]:
    """Prepare and invoke the exact V11 Studio entry, with no legacy fallback."""
    repo = Path(repo or default_v11_repo())
    prepared, out_dir, env = prepare_new_request(
        node_exe=node_exe, prompt=prompt, model=model, repo=repo,
        private_module=private_module, strata_repo=strata_repo)
    # Studio's Generate button is the user's explicit one-request approval.
    # Materialize it only after POST-free preparation; it never enters the
    # parent environment or any later Studio request.
    if cancel_event is not None and cancel_event.is_set():
        raise V11GenerationError('CANCELLED_BEFORE_STUDIO_NEW_POST')
    env['MAB_R125_STUDIO_NEW_APPROVAL'] = _studio_new_approval(prepared)
    repo = repo.resolve(strict=True)
    script = repo / 'scripts' / 'r125-local-architecture-plan-owned.mjs'
    if cancel_event is not None and cancel_event.is_set():
        env.pop('MAB_R125_STUDIO_NEW_APPROVAL', None)
        raise V11GenerationError('CANCELLED_BEFORE_STUDIO_NEW_POST')
    if on_execution_start is not None:
        on_execution_start()
    if cancel_event is not None and cancel_event.is_set():
        env.pop('MAB_R125_STUDIO_NEW_APPROVAL', None)
        raise V11GenerationError('CANCELLED_BEFORE_STUDIO_NEW_POST')
    mode = '--studio-new-bounded' if bounded else '--studio-new'
    code, receipt = _run_json([node_exe, str(script), mode,
                               prepared['requestSha256'], '--model', model,
                               '--prompt', prompt, '--out', str(out_dir)],
                              env=env, cwd=repo)
    if code != 0:
        reason = str(receipt.get('reason') or receipt.get('status') or 'V11_RUN_HELD')
        raise V11GenerationError('V11_STUDIO_RUN_HELD: ' + reason)
    validate_source_ready_receipt(receipt, request=prepared, out_dir=out_dir,
                                  bounded=bounded)
    return receipt, out_dir
