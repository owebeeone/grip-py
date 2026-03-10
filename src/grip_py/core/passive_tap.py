"""Passive tap implementation for shared projection hydration."""

from __future__ import annotations

from typing import Any

from .base_tap import BaseTap
from .grip import Grip
from .interfaces import SharedProjectionTapSpec


def _coerce_execution_mode(value: str | None) -> str:
    if value in {"replicated", "origin-primary", "negotiated-primary"}:
        return value
    return "replicated"


def _coerce_execution_role(value: str | None) -> str:
    if value in {"primary", "follower"}:
        return value
    return "follower"


class PassiveTap(BaseTap):
    """Non-executing tap used to materialize shared routed graph state."""

    def __init__(
        self,
        *,
        tap_id: str,
        tap_type: str,
        provides: tuple[Grip[Any], ...],
        execution_mode: str | None = None,
        execution_role: str | None = None,
        purpose: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            provides=provides,
            execution_mode=_coerce_execution_mode(execution_mode),
        )
        self.id = tap_id
        self.tap_id = tap_id
        self.tap_type = tap_type
        self.purpose = purpose
        self.description = description
        self.metadata = metadata
        self._values: dict[Grip[Any], Any] = {}
        self.set_execution_role(_coerce_execution_role(execution_role))

    def produce(self, *, dest_context=None) -> None:
        self.publish(dict(self._values), dest_context=dest_context)

    def set_shared_grip_value(self, grip: Grip[Any], value: Any) -> bool:
        if grip not in self.provides:
            return False
        self._values[grip] = value
        self.produce()
        return True


def create_passive_tap(
    spec: SharedProjectionTapSpec,
    provides: tuple[Grip[Any], ...],
) -> PassiveTap:
    return PassiveTap(
        tap_id=spec.tap_id,
        tap_type=spec.tap_type,
        provides=provides,
        execution_mode=spec.mode,
        execution_role=spec.role,
        purpose=spec.purpose,
        description=spec.description,
        metadata=spec.metadata,
    )
