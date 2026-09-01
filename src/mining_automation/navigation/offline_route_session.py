"""Single-head, input-disabled route-causality rehearsal sessions."""

from __future__ import annotations

import math
import re
from dataclasses import InitVar, dataclass, field
from enum import StrEnum
from threading import RLock
from typing import Final, Literal, NoReturn, SupportsIndex

from .contracts import (
    CheckpointObservation,
    NavigationFailureReason,
    NavigationPhase,
    NavigationTransition,
    RouteDirection,
    RouteEvaluationContext,
    RouteProgress,
    SyntheticStepAttemptReceipt,
    _snapshot_navigation_contract,
)
from .machine import (
    observe_checkpoint,
    prepare_step,
    record_step_attempt_receipt,
    start_route,
    stop_route,
)

__all__ = [
    "OfflineRouteSession",
    "OfflineRouteSessionPhase",
    "OfflineRouteSessionProgress",
    "OfflineRouteSessionResult",
    "OfflineRouteSessionSequencer",
    "OfflineRouteSessionStopReason",
]


_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_FACTORY_TOKEN: Final[object] = object()


class OfflineRouteSessionPhase(StrEnum):
    ACTIVE = "active"
    ARRIVED = "arrived"
    STOPPED = "stopped"


class OfflineRouteSessionStopReason(StrEnum):
    NAVIGATION_FAILURE = "navigation_failure"
    CHECKPOINT_TIMEOUT = "checkpoint_timeout"
    STEP_TIMEOUT = "step_timeout"
    ATTEMPT_TIMEOUT = "attempt_timeout"
    INTERRUPTED = "interrupted"
    SESSION_REPLACED = "session_replaced"


def _identifier(value: object, field_name: str) -> str:
    if type(value) is not str or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a portable non-empty identifier")
    return value


def _time(value: object, field_name: str) -> float:
    if (
        type(value) is not float
        or not math.isfinite(value)
        or value < 0
        or (value == 0.0 and math.copysign(1.0, value) < 0.0)
    ):
        raise ValueError(f"{field_name} must be an exact finite non-negative float")
    return value


@dataclass(frozen=True, slots=True)
class OfflineRouteSession:
    """One exact route, checkpoint-source, and attempt-source session."""

    session_id: str
    context: RouteEvaluationContext
    direction: RouteDirection
    live_navigation_enabled: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)
    automatic_retry_enabled: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        _identifier(self.session_id, "offline route session_id")
        if type(self.context) is not RouteEvaluationContext:
            raise ValueError("offline route session requires RouteEvaluationContext")
        if type(self.direction) is not RouteDirection:
            raise ValueError("offline route session direction must be RouteDirection")
        if self.direction is not self.context.plan.identity.direction:
            raise ValueError("offline route session direction differs from its exact plan")
        if (
            self.live_navigation_enabled is not False
            or self.input_authority is not False
            or self.automatic_retry_enabled is not False
        ):
            raise ValueError("offline route sessions cannot carry input or retry authority")


def _snapshot_session(session: OfflineRouteSession) -> OfflineRouteSession:
    if type(session) is not OfflineRouteSession:
        raise ValueError("offline route data must use exact OfflineRouteSession")
    if (
        session.live_navigation_enabled is not False
        or session.input_authority is not False
        or session.automatic_retry_enabled is not False
    ):
        raise ValueError("offline route session authority fields were mutated")
    return OfflineRouteSession(
        session_id=_identifier(session.session_id, "offline route session_id"),
        context=_snapshot_navigation_contract(session.context),
        direction=session.direction,
    )


@dataclass(frozen=True, slots=True)
class OfflineRouteSessionProgress:
    """Read-only snapshot issued from the sequencer's one current head."""

    session: OfflineRouteSession
    phase: OfflineRouteSessionPhase
    navigation: RouteProgress
    last_event_monotonic_s: float
    stop_reason: OfflineRouteSessionStopReason | None = None
    live_navigation_enabled: Literal[False] = field(default=False, init=False)
    input_authority: Literal[False] = field(default=False, init=False)
    automatic_retry_enabled: Literal[False] = field(default=False, init=False)
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise ValueError("offline route progress may only be issued by its sequencer")
        if type(self.session) is not OfflineRouteSession:
            raise ValueError("offline route progress requires OfflineRouteSession")
        if type(self.phase) is not OfflineRouteSessionPhase:
            raise ValueError("offline route phase must be OfflineRouteSessionPhase")
        if type(self.navigation) is not RouteProgress:
            raise ValueError("offline route progress requires RouteProgress")
        if (
            self.live_navigation_enabled is not False
            or self.input_authority is not False
            or self.automatic_retry_enabled is not False
        ):
            raise ValueError("offline route progress cannot carry input or retry authority")
        if self.navigation.context != self.session.context:
            raise ValueError("offline route progress contains a replaced navigation context")
        event_time = _time(self.last_event_monotonic_s, "offline route event time")
        if event_time < self.navigation.last_transition_monotonic_s:
            raise ValueError("offline route event time precedes the navigation state")
        if self.phase is OfflineRouteSessionPhase.ACTIVE:
            valid = (
                self.navigation.phase not in {NavigationPhase.ARRIVED, NavigationPhase.STOPPED}
                and self.stop_reason is None
            )
        elif self.phase is OfflineRouteSessionPhase.ARRIVED:
            valid = self.navigation.phase is NavigationPhase.ARRIVED and self.stop_reason is None
        else:
            valid = (
                self.navigation.phase is NavigationPhase.STOPPED
                and type(self.stop_reason) is OfflineRouteSessionStopReason
            )
        if not valid:
            raise ValueError("offline route session phase disagrees with its retained state")


@dataclass(frozen=True, slots=True)
class OfflineRouteSessionResult:
    progress: OfflineRouteSessionProgress
    navigation_transition: NavigationTransition | None = None
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise ValueError("offline route results may only be issued by the sequencer")
        if type(self.progress) is not OfflineRouteSessionProgress:
            raise ValueError("offline route result requires progress")
        if self.navigation_transition is not None:
            if (
                type(self.navigation_transition) is not NavigationTransition
                or self.navigation_transition.progress != self.progress.navigation
            ):
                raise ValueError("navigation transition differs from outer session progress")


def _snapshot_progress(progress: OfflineRouteSessionProgress) -> OfflineRouteSessionProgress:
    if type(progress) is not OfflineRouteSessionProgress:
        raise ValueError("offline route snapshot requires exact progress")
    if (
        progress.live_navigation_enabled is not False
        or progress.input_authority is not False
        or progress.automatic_retry_enabled is not False
    ):
        raise ValueError("offline route progress authority fields were mutated")
    return OfflineRouteSessionProgress(
        session=_snapshot_session(progress.session),
        phase=progress.phase,
        navigation=_snapshot_navigation_contract(progress.navigation),
        last_event_monotonic_s=_time(
            progress.last_event_monotonic_s,
            "offline route event time",
        ),
        stop_reason=progress.stop_reason,
        _factory_token=_FACTORY_TOKEN,
    )


def _snapshot_result(result: OfflineRouteSessionResult) -> OfflineRouteSessionResult:
    if type(result) is not OfflineRouteSessionResult:
        raise ValueError("offline route snapshot requires exact result")
    return OfflineRouteSessionResult(
        progress=_snapshot_progress(result.progress),
        navigation_transition=(
            None
            if result.navigation_transition is None
            else _snapshot_navigation_contract(result.navigation_transition)
        ),
        _factory_token=_FACTORY_TOKEN,
    )


class OfflineRouteSessionSequencer:
    """Own the only mutable head and the complete recovery lineage.

    Snapshots are deliberately not accepted by any mutating method.  A stale
    snapshot therefore cannot be used to fork, retry, or replace current
    progress.  Copying and pickling the sequencer are disabled for the same
    reason.
    """

    __slots__ = (
        "_lock",
        "_progress",
        "_used_attempt_ids",
        "_used_attempt_source_session_ids",
        "_used_capture_session_ids",
        "_used_route_session_ids",
    )

    def __init__(
        self,
        session: OfflineRouteSession,
        *,
        started_monotonic_s: float,
        _factory_token: object | None = None,
    ) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise ValueError("use OfflineRouteSessionSequencer.begin()")
        self._lock = RLock()
        started = _time(started_monotonic_s, "offline route start time")
        owned_session = _snapshot_session(session)
        navigation = start_route(owned_session.context, started_monotonic_s=started)
        self._progress = self._make_progress(
            owned_session,
            OfflineRouteSessionPhase.ACTIVE,
            navigation,
            started,
        )
        self._used_route_session_ids = {owned_session.session_id}
        self._used_capture_session_ids = {owned_session.context.expected_source.capture_session_id}
        self._used_attempt_source_session_ids = {
            owned_session.context.expected_attempt_source.session_id
        }
        self._used_attempt_ids: set[str] = set()

    @classmethod
    def begin(
        cls,
        session: OfflineRouteSession,
        *,
        started_monotonic_s: float,
    ) -> OfflineRouteSessionSequencer:
        """Begin one new, independent offline rehearsal lineage."""

        if type(session) is not OfflineRouteSession:
            raise ValueError("offline route sequencer requires OfflineRouteSession")
        return cls(
            session,
            started_monotonic_s=started_monotonic_s,
            _factory_token=_FACTORY_TOKEN,
        )

    @property
    def progress(self) -> OfflineRouteSessionProgress:
        with self._lock:
            return _snapshot_progress(self._progress)

    def __copy__(self) -> NoReturn:
        raise TypeError("offline route sequencers cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        del memo
        raise TypeError("offline route sequencers cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("offline route sequencers cannot be pickled")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("offline route sequencers cannot be pickled")

    @staticmethod
    def _make_progress(
        session: OfflineRouteSession,
        phase: OfflineRouteSessionPhase,
        navigation: RouteProgress,
        last_event_monotonic_s: float,
        stop_reason: OfflineRouteSessionStopReason | None = None,
    ) -> OfflineRouteSessionProgress:
        return OfflineRouteSessionProgress(
            session=session,
            phase=phase,
            navigation=navigation,
            last_event_monotonic_s=last_event_monotonic_s,
            stop_reason=stop_reason,
            _factory_token=_FACTORY_TOKEN,
        )

    def _terminal(self) -> OfflineRouteSessionResult:
        return _snapshot_result(
            OfflineRouteSessionResult(
                progress=self._progress,
                _factory_token=_FACTORY_TOKEN,
            )
        )

    def _stop(
        self,
        reason: OfflineRouteSessionStopReason,
        navigation_reason: NavigationFailureReason,
        evaluated_monotonic_s: float,
    ) -> OfflineRouteSessionResult:
        transition = stop_route(
            self._progress.navigation,
            navigation_reason,
            evaluated_monotonic_s=evaluated_monotonic_s,
        )
        event_time = transition.progress.last_transition_monotonic_s
        self._progress = self._make_progress(
            self._progress.session,
            OfflineRouteSessionPhase.STOPPED,
            transition.progress,
            event_time,
            reason,
        )
        return _snapshot_result(
            OfflineRouteSessionResult(
                progress=self._progress,
                navigation_transition=transition,
                _factory_token=_FACTORY_TOKEN,
            )
        )

    def _preflight(
        self,
        session: OfflineRouteSession,
        evaluated_monotonic_s: float,
        *,
        arrived_is_terminal: bool = True,
    ) -> tuple[OfflineRouteSessionResult | None, float]:
        if type(session) is not OfflineRouteSession:
            raise ValueError("offline route event requires OfflineRouteSession")
        supplied_session = _snapshot_session(session)
        evaluated = _time(evaluated_monotonic_s, "offline route evaluation time")
        if self._progress.phase is OfflineRouteSessionPhase.STOPPED:
            return self._terminal(), evaluated
        if evaluated < self._progress.last_event_monotonic_s:
            return (
                self._stop(
                    OfflineRouteSessionStopReason.NAVIGATION_FAILURE,
                    NavigationFailureReason.OUT_OF_ORDER_EVALUATION,
                    evaluated,
                ),
                evaluated,
            )
        if supplied_session != self._progress.session:
            return (
                self._stop(
                    OfflineRouteSessionStopReason.SESSION_REPLACED,
                    NavigationFailureReason.ROUTE_SESSION_REPLACED,
                    evaluated,
                ),
                evaluated,
            )
        if arrived_is_terminal and self._progress.phase is OfflineRouteSessionPhase.ARRIVED:
            return self._terminal(), evaluated
        return None, evaluated

    def _apply(
        self,
        transition: NavigationTransition,
        evaluated_monotonic_s: float,
    ) -> OfflineRouteSessionResult:
        if transition.progress.phase is NavigationPhase.STOPPED:
            phase = OfflineRouteSessionPhase.STOPPED
            stop_reason = OfflineRouteSessionStopReason.NAVIGATION_FAILURE
        elif transition.progress.phase is NavigationPhase.ARRIVED:
            phase = OfflineRouteSessionPhase.ARRIVED
            stop_reason = None
        else:
            phase = OfflineRouteSessionPhase.ACTIVE
            stop_reason = None
        self._progress = self._make_progress(
            self._progress.session,
            phase,
            transition.progress,
            evaluated_monotonic_s,
            stop_reason,
        )
        return _snapshot_result(
            OfflineRouteSessionResult(
                progress=self._progress,
                navigation_transition=transition,
                _factory_token=_FACTORY_TOKEN,
            )
        )

    def observe(
        self,
        session: OfflineRouteSession,
        observation: CheckpointObservation,
        *,
        evaluated_monotonic_s: float,
    ) -> OfflineRouteSessionResult:
        """Atomically evaluate one observation against the current head."""

        with self._lock:
            return self._observe(
                session,
                observation,
                evaluated_monotonic_s=evaluated_monotonic_s,
            )

    def _observe(
        self,
        session: OfflineRouteSession,
        observation: CheckpointObservation,
        *,
        evaluated_monotonic_s: float,
    ) -> OfflineRouteSessionResult:
        """Evaluate one observation while the session lock is held."""

        if type(observation) is not CheckpointObservation:
            raise ValueError("offline route observation must be CheckpointObservation")
        owned_observation = _snapshot_navigation_contract(observation)
        preflight, evaluated = self._preflight(
            session,
            evaluated_monotonic_s,
            arrived_is_terminal=False,
        )
        if preflight is not None:
            return preflight
        context = self._progress.session.context
        if self._progress.phase is OfflineRouteSessionPhase.ARRIVED:
            if (
                owned_observation.route != context.plan.identity
                or owned_observation.provenance.source != context.expected_source
            ):
                return self._stop(
                    OfflineRouteSessionStopReason.SESSION_REPLACED,
                    NavigationFailureReason.ROUTE_SESSION_REPLACED,
                    evaluated,
                )
            return self._terminal()
        transition = observe_checkpoint(
            context,
            self._progress.navigation,
            owned_observation,
            evaluated_monotonic_s=evaluated,
        )
        return self._apply(transition, evaluated)

    def prepare_step(
        self,
        session: OfflineRouteSession,
        *,
        attempt_id: str,
        evaluated_monotonic_s: float,
    ) -> OfflineRouteSessionResult:
        """Atomically consume current evidence into an input-disabled proposal."""

        with self._lock:
            return self._prepare_step(
                session,
                attempt_id=attempt_id,
                evaluated_monotonic_s=evaluated_monotonic_s,
            )

    def _prepare_step(
        self,
        session: OfflineRouteSession,
        *,
        attempt_id: str,
        evaluated_monotonic_s: float,
    ) -> OfflineRouteSessionResult:
        """Prepare one step while the session lock is held."""

        attempt = _identifier(attempt_id, "offline route attempt_id")
        preflight, evaluated = self._preflight(session, evaluated_monotonic_s)
        if preflight is not None:
            return preflight
        if attempt in self._used_attempt_ids:
            return self._stop(
                OfflineRouteSessionStopReason.NAVIGATION_FAILURE,
                NavigationFailureReason.DUPLICATE_ATTEMPT_ID,
                evaluated,
            )
        self._used_attempt_ids.add(attempt)
        context = self._progress.session.context
        transition = prepare_step(
            context,
            self._progress.navigation,
            attempt_id=attempt,
            evaluated_monotonic_s=evaluated,
        )
        return self._apply(transition, evaluated)

    def record_attempt(
        self,
        session: OfflineRouteSession,
        receipt: SyntheticStepAttemptReceipt | None,
        *,
        evaluated_monotonic_s: float,
    ) -> OfflineRouteSessionResult:
        """Atomically record one source-issued synthetic attempt boundary."""

        with self._lock:
            return self._record_attempt(
                session,
                receipt,
                evaluated_monotonic_s=evaluated_monotonic_s,
            )

    def _record_attempt(
        self,
        session: OfflineRouteSession,
        receipt: SyntheticStepAttemptReceipt | None,
        *,
        evaluated_monotonic_s: float,
    ) -> OfflineRouteSessionResult:
        """Record one attempt while the session lock is held."""

        preflight, evaluated = self._preflight(session, evaluated_monotonic_s)
        if preflight is not None:
            return preflight
        owned_receipt = None if receipt is None else _snapshot_navigation_contract(receipt)
        context = self._progress.session.context
        transition = record_step_attempt_receipt(
            context,
            self._progress.navigation,
            owned_receipt,
            evaluated_monotonic_s=evaluated,
        )
        return self._apply(transition, evaluated)

    def interrupt(
        self,
        session: OfflineRouteSession,
        *,
        evaluated_monotonic_s: float,
    ) -> OfflineRouteSessionResult:
        """Atomically fail-stop on an explicit caller-owned interruption."""

        with self._lock:
            return self._interrupt(
                session,
                evaluated_monotonic_s=evaluated_monotonic_s,
            )

    def _interrupt(
        self,
        session: OfflineRouteSession,
        *,
        evaluated_monotonic_s: float,
    ) -> OfflineRouteSessionResult:
        """Interrupt while the session lock is held."""

        preflight, evaluated = self._preflight(session, evaluated_monotonic_s)
        if preflight is not None:
            return preflight
        return self._stop(
            OfflineRouteSessionStopReason.INTERRUPTED,
            NavigationFailureReason.SESSION_INTERRUPTED,
            evaluated,
        )

    def timeout(
        self,
        session: OfflineRouteSession,
        *,
        evaluated_monotonic_s: float,
    ) -> OfflineRouteSessionResult:
        """Atomically fail-stop on one caller-recorded timeout event."""

        with self._lock:
            return self._timeout(
                session,
                evaluated_monotonic_s=evaluated_monotonic_s,
            )

    def _timeout(
        self,
        session: OfflineRouteSession,
        *,
        evaluated_monotonic_s: float,
    ) -> OfflineRouteSessionResult:
        """Apply one timeout while the session lock is held."""

        preflight, evaluated = self._preflight(session, evaluated_monotonic_s)
        if preflight is not None:
            return preflight
        phase = self._progress.navigation.phase
        if phase is NavigationPhase.AWAITING_CHECKPOINT:
            outer_reason = OfflineRouteSessionStopReason.CHECKPOINT_TIMEOUT
            inner_reason = NavigationFailureReason.CHECKPOINT_TIMEOUT
        elif phase is NavigationPhase.READY_FOR_STEP:
            outer_reason = OfflineRouteSessionStopReason.STEP_TIMEOUT
            inner_reason = NavigationFailureReason.STEP_TIMEOUT
        elif phase is NavigationPhase.AWAITING_ATTEMPT_RECEIPT:
            outer_reason = OfflineRouteSessionStopReason.ATTEMPT_TIMEOUT
            inner_reason = NavigationFailureReason.ATTEMPT_TIMEOUT
        else:  # pragma: no cover - outer progress validates reachable active phases
            raise AssertionError("active offline route has no timeout class")
        return self._stop(outer_reason, inner_reason, evaluated)

    def restart(
        self,
        replacement: OfflineRouteSession,
        *,
        started_monotonic_s: float,
    ) -> OfflineRouteSessionProgress:
        """Atomically begin one explicit fresh recovery session."""

        with self._lock:
            return self._restart(
                replacement,
                started_monotonic_s=started_monotonic_s,
            )

    def _restart(
        self,
        replacement: OfflineRouteSession,
        *,
        started_monotonic_s: float,
    ) -> OfflineRouteSessionProgress:
        """Restart while the session lock is held."""

        if type(replacement) is not OfflineRouteSession:
            raise ValueError("offline route restart requires OfflineRouteSession")
        owned_replacement = _snapshot_session(replacement)
        started = _time(started_monotonic_s, "offline route restart time")
        stopped = self._progress
        if stopped.phase is not OfflineRouteSessionPhase.STOPPED:
            raise ValueError("only a stopped offline route session can be restarted")
        if started <= stopped.last_event_monotonic_s:
            raise ValueError("offline route restart must be strictly after the stop")
        if owned_replacement.direction is not stopped.session.direction:
            raise ValueError("offline route recovery cannot silently reverse direction")
        prior_context = stopped.session.context
        replacement_context = owned_replacement.context
        if replacement_context.plan != prior_context.plan:
            raise ValueError(
                "offline route recovery requires the same exact route plan and version"
            )
        if replacement_context.policy != prior_context.policy:
            raise ValueError("offline route recovery cannot change navigation policy")
        prior_checkpoint_source = prior_context.expected_source
        replacement_checkpoint_source = replacement_context.expected_source
        if (
            replacement_checkpoint_source.detector != prior_checkpoint_source.detector
            or replacement_checkpoint_source.profile != prior_checkpoint_source.profile
            or replacement_checkpoint_source.frame_source_id
            != prior_checkpoint_source.frame_source_id
        ):
            raise ValueError("offline route recovery cannot change checkpoint source semantics")
        prior_attempt_source = prior_context.expected_attempt_source
        replacement_attempt_source = replacement_context.expected_attempt_source
        if (
            replacement_attempt_source.source_id != prior_attempt_source.source_id
            or replacement_attempt_source.version != prior_attempt_source.version
            or replacement_attempt_source.evidence_role is not prior_attempt_source.evidence_role
        ):
            raise ValueError("offline route recovery cannot change attempt source semantics")
        if owned_replacement.session_id in self._used_route_session_ids:
            raise ValueError("offline route recovery requires a globally new route session_id")
        capture_session_id = replacement_checkpoint_source.capture_session_id
        if capture_session_id in self._used_capture_session_ids:
            raise ValueError(
                "offline route recovery requires a globally fresh checkpoint source session"
            )
        attempt_source_session_id = replacement_attempt_source.session_id
        if attempt_source_session_id in self._used_attempt_source_session_ids:
            raise ValueError(
                "offline route recovery requires a globally fresh attempt source session"
            )

        navigation = start_route(replacement_context, started_monotonic_s=started)
        self._used_route_session_ids.add(owned_replacement.session_id)
        self._used_capture_session_ids.add(capture_session_id)
        self._used_attempt_source_session_ids.add(attempt_source_session_id)
        self._progress = self._make_progress(
            owned_replacement,
            OfflineRouteSessionPhase.ACTIVE,
            navigation,
            started,
        )
        return _snapshot_progress(self._progress)
