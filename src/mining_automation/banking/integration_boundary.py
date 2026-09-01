"""Reserved boundary for future source-owned navigation/inventory receipts.

Banking cannot infer authority from a duck-typed object. The current
navigation endpoint export is explicitly non-authoritative, and Inventory V3
does not yet publish a nominal release identity/receipt contract. Consequently
this module deliberately exports no adapters or structural protocols.

When those owning subsystems publish reviewed nominal contracts, a future
change may add exact-type adapters that preserve their release identity. Until
then, callers must not promote locally reconstructed checkpoint or inventory
fields into banking workflow evidence through an integration convenience API.
"""

from __future__ import annotations

__all__: list[str] = []
