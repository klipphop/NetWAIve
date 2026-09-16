from netwaive.gatekeeper import GatekeeperAgent

def test_inconsistent_relation_result_is_not_rendered_as_list():
    text = GatekeeperAgent._render_relation_result({"left_count":64,"right_count":0,"covered_count":0,"missing_count":64,"missing":[{"id":1}]})
    assert "incohérents" in text
    assert "None" not in text
