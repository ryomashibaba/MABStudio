"""SOURCE declaration review hints; no second geometry validator."""
from copy import deepcopy
from app.examples import example_library
from app.revisions import describe_design_changes


def test_material_change_is_visible_to_non_developer():
    old = example_library()
    new = deepcopy(old)
    new['elements'][0]['material'] = 'wood'
    assert describe_design_changes(old, new) == 'wallsの材質 stone→wood'


def test_changed_geometry_fields_are_named_without_claiming_quality():
    old = example_library()
    new = deepcopy(old)
    new['elements'][2]['origin'] = [7, 1, 2]
    assert describe_design_changes(old, new) == 'entranceの位置を変更'


def test_new_removed_and_many_changed_elements_bounded():
    old = example_library()
    new = deepcopy(old)
    new['elements'] = [deepcopy(e) for e in old['elements'][1:]]
    new['elements'].append({'id': 'roof', 'shape': 'box', 'mode': 'add',
                            'material': 'wood', 'origin': [0, 0, 0], 'size': [1, 1, 1]})
    new['elements'][0]['material'] = 'wood'
    new['elements'][1]['size'] = [2, 4, 2]
    assert describe_design_changes(old, new, max_items=2) == (
        'entranceの大きさを変更、glass-frontの材質 glass→wood、ほか2件')


def test_only_metadata_change_does_not_claim_source_geometry_change():
    old = example_library()
    new = deepcopy(old)
    new['brief'] = '改善した設計'
    assert '設計要素は変更なし' in describe_design_changes(old, new)
