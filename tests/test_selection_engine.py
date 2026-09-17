from netwaive.selection_engine import SelectionEngine


def test_selection_engine_deduplicates_and_excludes_without_business_rules():
    source = {"count": 3, "results": [
        {"id": 1, "display": "A"},
        {"id": 1, "display": "A duplicate"},
        {"id": 2, "name": "B"},
    ]}
    result = SelectionEngine().build(source, endpoint="dcim/things", exclude_keys={"dcim/things/2": "explicit user exclusion"})
    assert [item.key for item in result.selected] == ["dcim/things/1"]
    assert [item.key for item in result.excluded] == ["dcim/things/2"]
    assert not result.duplicates


def test_selection_engine_marks_existing_objects_as_duplicates():
    result = SelectionEngine().build([{"id": 7, "display": "Existing"}], endpoint="ipam/things", duplicate_keys={"ipam/things/7"})
    assert not result.selected
    assert result.duplicates[0].object_id == 7
