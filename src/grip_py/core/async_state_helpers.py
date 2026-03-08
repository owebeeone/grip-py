"""Helper utilities for interpreting AsyncTap request states."""

from __future__ import annotations

import time

from .async_tap import RequestState


def has_data(state: RequestState) -> bool:
    return state.type in {"success", "stale-while-revalidate", "stale-with-error"}


def is_stale(state: RequestState) -> bool:
    return state.type in {"stale-while-revalidate", "stale-with-error"}


def is_refreshing(state: RequestState) -> bool:
    return state.type in {"loading", "stale-while-revalidate"}


def is_refreshing_with_data(state: RequestState) -> bool:
    return state.type == "stale-while-revalidate"


def has_error(state: RequestState) -> bool:
    return state.type in {"error", "stale-with-error"}


def get_error(state: RequestState):
    return state.error if has_error(state) else None


def is_loading(state: RequestState) -> bool:
    return state.type == "loading"


def is_idle(state: RequestState) -> bool:
    return state.type == "idle"


def get_data_retrieved_at(state: RequestState) -> float | None:
    if state.type in {"success", "stale-while-revalidate", "stale-with-error"}:
        return state.retrieved_at
    return None


def get_request_initiated_at(state: RequestState) -> float | None:
    if state.type == "loading":
        return state.initiated_at
    if state.type == "stale-while-revalidate":
        return state.refresh_initiated_at
    return None


def get_error_failed_at(state: RequestState) -> float | None:
    if state.type in {"error", "stale-with-error"}:
        return state.failed_at
    return None


def has_scheduled_retry(state: RequestState) -> bool:
    return state.retry_at is not None and state.retry_at > time.time()


def get_retry_time_remaining(state: RequestState) -> float | None:
    if state.retry_at is None:
        return None
    remaining = state.retry_at - time.time()
    return remaining if remaining > 0 else 0.0


def get_status_message(state: RequestState) -> str:
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
