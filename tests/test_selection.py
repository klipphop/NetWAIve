import pytest
from netwaive.selection import SelectionResult, selection_from_payload
from netwaive.gatekeeper import GatekeeperAgent


def _item(key, label, object_id=1):
    return {"key": key, "label": label, "object_id": object_id}


def test_selection_requires_selected_candidates_and_reasons():
    result = selection_from_payload({
        "candidates": [_item("a", "A"), _item("b", "B")],
        "selected": [_item("a", "A")],
        "excluded": [{"key": "b", "reason": "explicit exclusion"}],
        "duplicates": [],
    })
    assert result.selected[0].key == "a"
    assert result.excluded[0].reason == "explicit exclusion"


def test_selection_rejects_selected_duplicate():
    with pytest.raises(ValueError, match="cannot be selected"):
        selection_from_payload({"candidates": [_item("a", "A")], "selected": [_item("a", "A")], "duplicates": [_item("a", "A")]})


def test_empty_selected_creation_is_rejected():
    with pytest.raises(ValueError, match="no selected"):
        GatekeeperAgent._batch({"selection": {"candidates": [_item("a", "A")], "selected": [], "excluded": [], "duplicates": []}, "operations": [{"method":"POST", "endpoint":"dcim/sites", "data":{"name":"A"}}]})
