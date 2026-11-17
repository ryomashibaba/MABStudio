"""An app-owned static *input* to the actual B143 engine, not a voxel builder.

Used only for a first-use offline demonstration without starting an LLM.
"""
from .core import SCHEMA


def example_library() -> dict:
    return {
        "schema": SCHEMA,
        "id": "studio-library-sample",
        "title": "サンプル：小さな図書館",
        "brief": "オフライン実演用の直方体・窓・入口。AI生成ではない参考設計。",
        "designOnly": True,
        "bounds": {"width": 20, "height": 16, "depth": 20},
        "elements": [
            {"id":"walls", "shape":"box", "mode":"add", "material":"stone",
             "origin":[2,1,2], "size":[16,9,16], "shell":1},
            {"id":"glass-front", "shape":"box", "mode":"add", "material":"glass",
             "origin":[3,4,2], "size":[14,3,1]},
            {"id":"entrance", "shape":"box", "mode":"cut",
             "origin":[8,1,2], "size":[3,4,2]}
        ]
    }
