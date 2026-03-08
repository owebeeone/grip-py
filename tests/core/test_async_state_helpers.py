from __future__ import annotations

import time

from grip_py.core.async_state_helpers import (
    get_data_retrieved_at,
    get_error,
    get_error_failed_at,
    get_request_initiated_at,
    get_retry_time_remaining,
    get_status_message,
    has_data,
    has_error,
    has_scheduled_retry,
    is_idle,
    is_loading,
    is_refreshing,
    is_refreshing_with_data,
    is_stale,
)
from grip_py.core.async_tap import RequestState


def test_async_state_helpers_cover_all_primary_states() -> None:
    now = time.time()

    idle = RequestState(type="idle")
    loading = RequestState(type="loading", initiated_at=now)
    success = RequestState(type="success", retrieved_at=now)
    stale_refresh = RequestState(
        type="stale-while-revalidate",
        retrieved_at=now - 1,
        refresh_initiated_at=now,
    )
    err = RequestState(type="error", error=RuntimeError("boom"), failed_at=now)
    stale_err = RequestState(
        type="stale-with-error",
        retrieved_at=now - 2,
        error=RuntimeError("stale-boom"),
        failed_at=now,
    )

    assert is_idle(idle)
    assert is_loading(loading)
    assert has_data(success)
    assert has_data(stale_refresh)
    assert has_data(stale_err)
    assert is_stale(stale_refresh)
    assert is_stale(stale_err)
    assert is_refreshing(loading)
    assert is_refreshing(stale_refresh)
    assert is_refreshing_with_data(stale_refresh)
    assert has_error(err)
    assert has_error(stale_err)
    assert get_error(err) is not None
    assert get_error(stale_err) is not None
    assert get_data_retrieved_at(success) is not None
    assert get_data_retrieved_at(stale_refresh) is not None
    assert get_data_retrieved_at(stale_err) is not None
    assert get_request_initiated_at(loading) is not None
    assert get_request_initiated_at(stale_refresh) is not None
    assert get_error_failed_at(err) is not None
    assert get_error_failed_at(stale_err) is not None
    assert isinstance(get_status_message(success), str)


def test_retry_helpers_handle_scheduling() -> None:
    future = time.time() + 0.25
    state = RequestState(type="error", error=RuntimeError("retry"), retry_at=future)

    assert has_scheduled_retry(state)
    remaining = get_retry_time_remaining(state)
    assert remaining is not None
    assert remaining >= 0
