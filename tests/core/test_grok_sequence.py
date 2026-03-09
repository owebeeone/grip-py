from grip_py.core.grok import Grok
from grip_py.core.grip import GripRegistry


def test_allocate_origin_mutation_seq_is_monotonic() -> None:
    grok = Grok(GripRegistry())

    assert grok.get_last_origin_mutation_seq() == 0
    assert grok.allocate_origin_mutation_seq() == 1
    assert grok.allocate_origin_mutation_seq() == 2
    assert grok.allocate_origin_mutation_seq() == 3
    assert grok.get_last_origin_mutation_seq() == 3
