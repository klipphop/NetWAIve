from netwaive.query_models import QueryResult, Evidence
import pytest


def test_query_result_rejects_inconsistent_total():
    result = QueryResult(kind="comparison", title="x", total=2, items=[{"id": 1}], evidence=[Evidence(tool="x", endpoint="x", object_count=2)])
    with pytest.raises(ValueError): result.assert_consistent()


def test_query_result_requires_read_only_evidence():
    result = QueryResult(kind="collection", title="x", total=0, items=[], evidence=[])
    with pytest.raises(ValueError): result.assert_consistent()
