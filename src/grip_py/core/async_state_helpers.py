"""Helper utilities for interpreting AsyncTap request states."""

from __future__ import annotations

import time

from .async_tap import RequestState


def has_data(state: RequestState) -> bool:
    """Return ``True`` when the state contains usable data."""
    return state.type in {"success", "stale-while-revalidate", "stale-with-error"}


def is_stale(state: RequestState) -> bool:
    """Return ``True`` when state carries stale data."""
    return state.type in {"stale-while-revalidate", "stale-with-error"}


def is_refreshing(state: RequestState) -> bool:
    """Return ``True`` while a request is currently in progress."""
    return state.type in {"loading", "stale-while-revalidate"}


def is_refreshing_with_data(state: RequestState) -> bool:
    """Return ``True`` when refreshing while still showing stale data."""
    return state.type == "stale-while-revalidate"


def has_error(state: RequestState) -> bool:
    """Return ``True`` when the state includes an error."""
    return state.type in {"error", "stale-with-error"}


def get_error(state: RequestState) -> Exception | None:
    """Return the state error, if the state is an error state."""
    return state.error if has_error(state) else None


def is_loading(state: RequestState) -> bool:
    """Return ``True`` for the initial loading state."""
    return state.type == "loading"


def is_idle(state: RequestState) -> bool:
    """Return ``True`` when no request has started yet."""
    return state.type == "idle"


def get_data_retrieved_at(state: RequestState) -> float | None:
    """Return the retrieval timestamp for states with data."""
    if state.type in {"success", "stale-while-revalidate", "stale-with-error"}:
        return state.retrieved_at
    return None


def get_request_initiated_at(state: RequestState) -> float | None:
    """Return the in-flight request start timestamp when available."""
    if state.type == "loading":
        return state.initiated_at
    if state.type == "stale-while-revalidate":
        return state.refresh_initiated_at
    return None


def get_error_failed_at(state: RequestState) -> float | None:
    """Return the failure timestamp for error states."""
    if state.type in {"error", "stale-with-error"}:
        return state.failed_at
    return None


def has_scheduled_retry(state: RequestState) -> bool:
    """Return ``True`` when retry_at is set in the future."""
    return state.retry_at is not None and state.retry_at > time.time()


def get_retry_time_remaining(state: RequestState) -> float | None:
    """Return seconds until retry, or ``0.0`` if retry time has passed."""
    if state.retry_at is None:
        return None
    remaining = state.retry_at - time.time()
    return remaining if remaining > 0 else 0.0


def get_status_message(state: RequestState) -> str:
    """Return a short UI-friendly status label for the state."""
    if state.type == "idle":
        return "Ready"
    if state.type == "loading":
        return "Loading..."
    if state.type == "success":
        return "Loaded"
    if state.type == "error":
        return f"Error: {state.error}" if state.error else "Error"
    if state.type == "stale-while-revalidate":
        return "Refreshing..."
    if state.type == "stale-with-error":
        return f"Stale (Error: {state.error})" if state.error else "Stale (Error)"
    return state.type
