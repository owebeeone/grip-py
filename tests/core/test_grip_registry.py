import pytest

from grip_py import DuplicateGrip, GripRegistry


def test_add_infers_type_from_default():
    registry = GripRegistry()
    g = registry.add("UserAge", 42)

    assert g.default == 42
    assert g.data_type is int


def test_add_value_type_with_omitted_default_is_none():
    registry = GripRegistry()
    g = registry.add("TempC", value_type=float)

    assert g.default is None
    assert g.data_type is float


def test_add_value_type_with_none_and_nullable_true():
    registry = GripRegistry()
    g = registry.add("OptionalRate", None, value_type=float, nullable=True)

    assert g.default is None
    assert g.data_type is float


def test_add_with_conversion():
    registry = GripRegistry()
    g = registry.add("Zar", 5, value_type=float)

    assert g.default == 5.0
    assert g.data_type is float


def test_add_with_conversion_and_nullable_true():
    registry = GripRegistry()
    g = registry.add("Bar", 5, value_type=float, nullable=True)

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


def test_duplicate_name_raises():
    registry = GripRegistry()
    registry.add("Same", 1)
    with pytest.raises(DuplicateGrip):
        registry.add("Same", 2)


def test_none_with_value_type_nullable_false_raises():
    registry = GripRegistry()
    with pytest.raises(TypeError):
        registry.add("NotNullable", None, value_type=float, nullable=False)

