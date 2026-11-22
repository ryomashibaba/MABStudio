"""Source-cell selection bounds are enforced before a draft enters review."""
from __future__ import annotations

import pytest

from app.core import StudioError
from app.revisions import assert_changes_within_scope, normalize_edit_scope


def _scene(cells):
    return {
        'designOnly': True, 'readiness': 'NOT_PREPARED',
        'worldCoordinates': None, 'worldContacts': 0,
        'BuildPlan': {'operations': []},
        'stats': {'constructionApproved': False, 'voxels': len(cells)},
        'layerNames': ['stone', 'glass'], 'primitives': cells,
        'sourceSha256': 'a' * 64, 'geometrySha256': 'b' * 64,
    }


def test_selected_cube_allows_only_add_remove_and_material_changes_inside():
    before = _scene([[0, 0, 0, 0, 0], [1, 0, 0, 0, 0], [9, 0, 0, 0, 0]])
    after = _scene([[0, 0, 0, 1, 0], [2, 0, 0, 0, 0], [9, 0, 0, 0, 0]])
    scope = {'selected': [1, 0, 0], 'radius': 1,
             'min': [0, -1, -1], 'max': [2, 1, 1]}

    delta = assert_changes_within_scope(before, after, scope)

    assert delta['counts']['replaced'] == 1
    assert delta['counts']['removed'] == 1
    assert delta['counts']['added'] == 1


def test_selected_cube_holds_any_outside_cell_change():
    before = _scene([[0, 0, 0, 0, 0], [9, 0, 0, 0, 0]])
    after = _scene([[0, 0, 0, 1, 0], [9, 0, 0, 1, 0]])
    scope = {'selected': [0, 0, 0], 'radius': 0,
             'min': [0, 0, 0], 'max': [0, 0, 0]}

    with pytest.raises(StudioError, match='範囲外'):
        assert_changes_within_scope(before, after, scope)


def test_edit_scope_requires_cube_coordinates_to_match_selected_cell():
    with pytest.raises(StudioError, match='座標'):
        normalize_edit_scope({'selected': [2, 3, 4], 'radius': 1,
                              'min': [1, 2, 3], 'max': [3, 4, 6]})
