import pytest

import shutdown


@pytest.fixture(autouse=True)
def _reset():
    shutdown.reset()
    yield
    shutdown.reset()


def test_request_outside_deferred_exits_at_once():
    with pytest.raises(SystemExit):
        shutdown.request()
    assert shutdown.requested()


def test_request_inside_deferred_waits_for_block_end():
    done = []
    with pytest.raises(SystemExit):
        with shutdown.deferred():
            shutdown.request()      # postponed
            done.append("rest of sweep")
    assert done == ["rest of sweep"]


def test_nested_blocks_exit_only_at_outermost():
    seen = []
    with pytest.raises(SystemExit):
        with shutdown.deferred():
            with shutdown.deferred():
                shutdown.request()
            seen.append("outer continues")
    assert seen == ["outer continues"]


def test_no_request_no_exit():
    with shutdown.deferred():
        pass
    assert not shutdown.requested()
    assert shutdown.remaining() == float("inf")


def test_budget_counts_down(monkeypatch):
    monkeypatch.setattr(shutdown, "SHUTDOWN_BUDGET_SECONDS", 20.0)
    with pytest.raises(SystemExit):
        shutdown.request()
    assert 19.0 < shutdown.remaining() <= 20.0
