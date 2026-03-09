from grip_py import Grip, GripRegistry


def test_grip_fields_set():
    registry = GripRegistry()
    g = registry.add("UserName", "")

    assert isinstance(g, Grip)
    assert g.scope == "app"
    assert g.name == "UserName"
    assert g.key == "app:UserName"
    assert g.default == ""
    assert g.data_type is str


def test_grip_identity_semantics():
    registry = GripRegistry()
    g1 = registry.add("A", 1)
    g2 = registry.add("B", 1)

    assert g1 is g1
    assert g1 is not g2
    assert g1 != g2
