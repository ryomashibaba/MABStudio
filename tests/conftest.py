"""Only unit-test fixtures may monkeypatch the approved executable source hash."""
import hashlib
import pytest
from app import core

FAKE_MODULE_BYTES = None

@pytest.fixture(autouse=True)
def fake_engine_approval(monkeypatch, request):
    if request.node.get_closest_marker("real_engine") is not None:
        return
    from tests.test_core import FAKE_MODULE
    digest = hashlib.sha256(FAKE_MODULE.encode("utf-8")).hexdigest()
    monkeypatch.setattr(core, "APPROVED_B143_SOURCE_SHA256", digest)
    monkeypatch.setattr(core, "PINNED_SMOKE_GEOMETRY_SHA256", "geo")
