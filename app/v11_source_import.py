"""Read an external V11 B143 design as data, never execute or convert it.

The installed SHA-pinned B143 engine remains the sole SOURCE validator.
An import is an unapproved proposal and cannot write a Minecraft world.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.core import SCHEMA, StudioError

MAX_SOURCE_JSON_BYTES = 1024 * 1024


def read_external_b143_source(filename: str | Path) -> dict:
    source = Path(filename)
    if source.suffix.lower() != '.json':
        raise StudioError('外部SOURCEはB143_DESIGN.jsonなどの.jsonを選んでください')
    try:
        with source.open('rb') as handle:
            data = handle.read(MAX_SOURCE_JSON_BYTES + 1)
    except OSError as exc:
        raise StudioError('外部SOURCEを読み込めません: ' + str(exc)) from exc
    if len(data) > MAX_SOURCE_JSON_BYTES:
        raise StudioError('外部SOURCEが1MiBを超えています。読み込みを中止しました')
    try:
        design = json.loads(data.decode('utf-8-sig'))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise StudioError('外部SOURCEがUTF-8の正しいJSONではありません') from exc
    if not isinstance(design, dict) or design.get('schema') != SCHEMA or design.get('designOnly') is not True:
        raise StudioError('B143設計JSONではありません。V11の計画JSONや検証レシートは読み込めません')
    # A newer V11 compiler supports repeat/staircase. Studio's frozen B143
    # does not. Reject explicitly rather than silently flattening/guessing.
    elements = design.get('elements')
    if isinstance(elements, list) and any(
            isinstance(part, dict) and ('repeat' in part or part.get('shape') == 'staircase')
            for part in elements):
        raise StudioError('V11のrepeat/staircaseは固定B143に未対応です。'
                          'SOURCEを変換・自動修正せず、読み込みを保留しました')
    # Shape, bounds, materials, hashes and safety are checked by run_bridge.
    return design
