from grip_py import Drip, use_grip


class _FakeGrok:
    def __init__(self, drip: Drip[int]):
        self._drip = drip

    def query(self, grip, ctx):
        _ = grip
        _ = ctx
        return self._drip


def test_use_grip_reads_drip_snapshot():
    drip = Drip[int](7)
    grok = _FakeGrok(drip)

    assert use_grip(grok, grip="AnyGrip", ctx=None) == 7

    drip.next(9)
    assert use_grip(grok, grip="AnyGrip", ctx=None) == 9
