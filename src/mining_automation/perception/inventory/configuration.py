"""Safe construction of the reviewed-profile inventory detector."""

from __future__ import annotations

from ...capture import Frame
from .classification import (
    ClassificationPolicy,
    InventoryClassificationError,
    ReferenceInventoryClassifier,
)
from .detector import InventoryDetector
from .localization import ExactProfileInventoryLocator, InventoryFrameProfile
from .positive_classifier_v2 import (
    InventoryPositiveDetectorV2,
    PositiveReferenceInventoryClassifierV2,
)

__all__ = [
    "inventory_detector_from_profile",
    "inventory_positive_detector_v2_from_profile",
]


def inventory_detector_from_profile(
    profile: InventoryFrameProfile,
    empty_reference: Frame,
    *,
    policy: ClassificationPolicy | None = None,
    localization_threshold: float = 0.9,
    minimum_slot_confidence: float = 0.8,
) -> InventoryDetector:
    """Build one detector from a reviewed profile and its empty reference.

    The profile is the single source of truth for frame geometry, inventory
    region, and grid layout. Requiring the complete reference frame to match
    that geometry prevents a valid-looking crop or a different-geometry client
    mode from being silently bound to the profile.
    """
    if not isinstance(profile, InventoryFrameProfile):
        raise TypeError(
            "profile must be an InventoryFrameProfile, "
            f"got {type(profile).__name__}"
        )
    if not isinstance(empty_reference, Frame):
        raise InventoryClassificationError("empty_reference must be a Frame")
    if (empty_reference.width, empty_reference.height) != (
        profile.frame_width,
        profile.frame_height,
    ):
        raise InventoryClassificationError(
            "empty reference frame geometry must match the reviewed profile: "
            f"got {empty_reference.width}x{empty_reference.height}, expected "
            f"{profile.frame_width}x{profile.frame_height}"
        )

    classifier = ReferenceInventoryClassifier(
        empty_reference,
        profile.region,
        profile.layout,
        policy,
    )
    return InventoryDetector(
        locator=ExactProfileInventoryLocator((profile,)),
        classifier=classifier,
        localization_threshold=localization_threshold,
        minimum_slot_confidence=minimum_slot_confidence,
    )


def inventory_positive_detector_v2_from_profile(
    profile: InventoryFrameProfile,
    empty_reference: Frame,
) -> InventoryPositiveDetectorV2:
    """Build the frozen, non-activating positive-classifier V2 candidate.

    This factory intentionally has no policy or threshold parameters.  It
    reuses the reviewed profile/reference validation from the V1 factory while
    preserving the fixed 0.8 detector publication floor.
    """
    baseline = inventory_detector_from_profile(profile, empty_reference)
    classifier = PositiveReferenceInventoryClassifierV2(
        empty_reference,
        profile.region,
        profile.layout,
    )
    return InventoryPositiveDetectorV2(
        locator=baseline.locator,
        classifier=classifier,
        localization_threshold=baseline.localization_threshold,
        minimum_slot_confidence=baseline.minimum_slot_confidence,
    )
