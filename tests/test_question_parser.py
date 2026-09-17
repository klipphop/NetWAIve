from netwaive.gatekeeper import GatekeeperAgent


def test_parse_boolean_options():
    text, quick, question = GatekeeperAgent._parse_question("Le site existe-t-il ? [OPTIONS: Oui | Non]")
    assert text == "Le site existe-t-il ?"
    assert quick == ["Oui", "Non"]
    assert question.kind == "boolean"


def test_parse_choice_options():
    _text, _quick, question = GatekeeperAgent._parse_question("Quel rôle ? [OPTIONS: Switch | Router]")
    assert question.kind == "choice"
    assert [item.label for item in question.options] == ["Switch", "Router"]
