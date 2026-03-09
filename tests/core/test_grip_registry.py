import pytest

from grip_py import DuplicateGrip, GripRegistry


def test_add_infers_type_from_default():
    registry = GripRegistry()
    g = registry.add("UserAge", 42)

    assert g.scope == "app"
    assert g.key == "app:UserAge"
    assert g.default == 42
    assert g.data_type is int


def test_add_value_type_with_omitted_default_is_none():
    registry = GripRegistry()
    g = registry.add("TempC", value_type=float)

    assert g.scope == "app"
    assert g.key == "app:TempC"
    assert g.default is None
    assert g.data_type is float


def test_add_value_type_with_none_and_nullable_true():
    registry = GripRegistry()
    g = registry.add("OptionalRate", None, value_type=float, nullable=True)

    assert g.scope == "app"
    assert g.key == "app:OptionalRate"
    assert g.default is None
    assert g.data_type is float


def test_add_with_conversion():
    registry = GripRegistry()
    g = registry.add("Zar", 5, value_type=float)

    assert g.scope == "app"
    assert g.key == "app:Zar"
    assert g.default == 5.0
    assert g.data_type is float


def test_add_with_conversion_and_nullable_true():
    registry = GripRegistry()
    g = registry.add("Bar", 5, value_type=float, nullable=True)

    assert g.scope == "app"
    assert g.key == "app:Bar"
    assert g.default == 5.0
    assert g.data_type is float


def test_add_conversion_failure_raises():
    registry = GripRegistry()
    with pytest.raises((TypeError, ValueError)):
        registry.add("BrokenInt", "abc", value_type=int)


def test_add_none_without_value_type_raises():
    registry = GripRegistry()
    with pytest.raises(TypeError):
        registry.add("BadNone", None)


def test_add_name_only_raises():
    registry = GripRegistry()
    with pytest.raises(TypeError):
        registry.add("Bad")


def test_duplicate_scope_and_name_raises():
    registry = GripRegistry()
    registry.add("Same", 1)
    with pytest.raises(DuplicateGrip):
        registry.add("Same", 2)


def test_same_name_in_different_scopes_is_allowed():
    registry = GripRegistry()
    g1 = registry.add("Theme", "light")
    g2 = registry.add("Theme", "dark", scope="session")

    assert g1.key == "app:Theme"
    assert g2.key == "session:Theme"
    assert g1 is not g2


def test_lookup_helpers_resolve_by_scope_name_and_key():
    registry = GripRegistry()
    app_theme = registry.add("Theme", "light")
    session_theme = registry.add("Theme", "dark", scope="session")

    assert registry.get("app", "Theme") is app_theme
    assert registry.get("session", "Theme") is session_theme
    assert registry.get_by_key("app:Theme") is app_theme
    assert registry.get_by_key("session:Theme") is session_theme
    assert registry.get("missing", "Theme") is None
    assert registry.get_by_key("missing:Theme") is None


def test_find_or_add_uses_default_scope():
    registry = GripRegistry()

    g1 = registry.find_or_add("Counter", 0)
    g2 = registry.find_or_add("Counter", 999)

    assert g1 is g2
    assert g1.scope == "app"
    assert g1.key == "app:Counter"
    assert g1.default == 0


def test_find_or_add_scoped():
    registry = GripRegistry()

    g1 = registry.find_or_add("Counter", 0, scope="session")
    g2 = registry.find_or_add("Counter", 999, scope="session")

    assert g1 is g2
    assert g1.scope == "session"
    assert g1.key == "session:Counter"
    assert g1.default == 0


def test_none_with_value_type_nullable_false_raises():
    registry = GripRegistry()
    with pytest.raises(TypeError):
        registry.add("NotNullable", None, value_type=float, nullable=False)
