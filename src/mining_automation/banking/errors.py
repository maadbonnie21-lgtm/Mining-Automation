"""Typed failures for the banking foundation seam.

Domain-level fail-closed outcomes (bank still closed, inventory unknown, stale
evidence, mismatched provenance, and so on) are represented as
:class:`~mining_automation.banking.contracts.BankingBlocker` values on a
result object, not as exceptions -- a workflow denying an action is an
ordinary, expected outcome, not a programming error. Exceptions here are
reserved for violations of the module's own API contract: a detector that
returns the wrong type, raises, or otherwise cannot be trusted to have run at
all.
"""

from __future__ import annotations

__all__ = [
    "BankDetectorContractError",
    "BankDetectorExecutionError",
    "BankingError",
]


class BankingError(Exception):
    """Base class for failures at the banking foundation boundary."""


class BankDetectorContractError(BankingError):
    """A bank detector or its output violated the public detector contract."""


class BankDetectorExecutionError(BankingError):
    """A bank detector raised while processing a frame.

    The original exception is retained as ``__cause__`` by the guarded runner.
    """
