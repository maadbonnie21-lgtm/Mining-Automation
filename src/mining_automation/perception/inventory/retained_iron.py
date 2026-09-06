"""Positive iron evidence for the retained mining experiment, not a C release.

The frozen V2/V3 candidates remain unchanged. V2 rejects this observed iron
sprite at its perimeter; V3 has no exact full-slot prototype for it. This
additional narrow recognizer requires a full, exact known iron template after
normalizing only source-proven background positions to a sentinel. It never
infers an item from failure to recognize empty, and never learns from a frame.

Source: diagnostics/third-rock-ore4-20260903/ore-04-reacquired.png,
BGRA SHA256 394049af97149b0d3c61e4306614b6c595b040e205bf9fce0a41ae424aa91778,
slots 0..3. The background palette is from the existing packaged empty profile.
This is replay-validated experimental recognition, not an approved C campaign,
production release, or proof of live 0->28. Unseen sprite variants remain UNKNOWN.
"""

from __future__ import annotations

import hashlib
from typing import Final

from ...capture import Frame, PixelFormat
from .classification import SlotOccupancy
from .geometry import InventoryGridLayout, Region
from .positive_classifier_v3 import InventoryPositiveV3DevelopmentResult
from .positive_v3_prototypes import (
    SUPPORTED_COLUMN_STRIDE,
    SUPPORTED_FRAME_HEIGHT,
    SUPPORTED_FRAME_WIDTH,
    SUPPORTED_PROFILE_ID,
    SUPPORTED_REGION,
    SUPPORTED_ROW_STRIDE,
)

BACKGROUND_RGB: Final[frozenset[tuple[int, int, int]]] = frozenset((
    (59, 50, 38),
    (59, 50, 39),
    (60, 51, 39),
    (61, 52, 40),
    (62, 52, 40),
    (62, 53, 40),
    (62, 53, 41),
    (62, 53, 42),
    (63, 53, 42),
    (63, 54, 43),
    (64, 54, 43),
    (64, 54, 44),
    (64, 55, 44),
    (64, 55, 45),
    (64, 56, 44),
    (64, 56, 45),
))

_BACKGROUND_MASK: Final[bytes] = bytes.fromhex(
    "ffffffffffffffffff0fffffff0702ffff0300feff0300fcff0300c0ff030080ff0300000f000000"
    "07000000030000000300000001006000010040000000000000000080000000c0000000e0000000e0"
    "000000e0000000e0000000e0000000c0000008e0810118f0e30138feff01f0ffff01f8ffff01f8ff"
    "ff03fcffff07feff"
)

IRON_TEMPLATE_SHA256: Final[frozenset[str]] = frozenset((
    "76ae2e3cb0b47ee5352b22f32bb93404211a955971874c5b6d40f115c5110349",
    "21277a9c9f4a95043c62da1d521cfe704c38137b52052bab76ee8fa38bb325dd",
    "29e5339c767ceca5e69477c487d7ed34eaa0065fbb7a5e21727d6a164229e973",
    "afec9102fd6fb292b1cb13a80177832cbce6ee043481f6f66fe636da23bade0b",
))


def _positive_iron_slot(frame: Frame, slot: Region) -> bool:
    normalized = bytearray()
    for y in range(32):
        for x in range(32):
            index = y * 32 + x
            offset = ((slot.y + y) * frame.width + slot.x + x) * 4
            blue, green, red = frame.payload[offset:offset + 3]
            rgb = (red, green, blue)
            if _BACKGROUND_MASK[index // 8] & (1 << (index % 8)):
                if rgb not in BACKGROUND_RGB:
                    return False
                normalized.extend((0, 0, 0))
            else:
                normalized.extend(rgb)
    return hashlib.sha256(normalized).hexdigest() in IRON_TEMPLATE_SHA256


def retained_iron_count(
    frame: Frame,
    guarded: InventoryPositiveV3DevelopmentResult,
) -> int | None:
    """Recognize a nonempty prefix only with fresh guard and positive slot proof.

    ``guarded`` must be the existing analyzer result for this same ``frame``.
    Geometry and external/gutter guards must already have passed; they yield
    zero slots on failure. Unchanged raw empty decisions retain the 0.8 floor.
    Every occupied slot requires an exact complete positive template. Returning
    None supplies no alternative evidence and grants no downstream authority.
    """
    if (
        (frame.width, frame.height) != (SUPPORTED_FRAME_WIDTH, SUPPORTED_FRAME_HEIGHT)
        or frame.pixel_format is not PixelFormat.BGRA8888
        or len(guarded.slots) != 28
    ):
        return None
    layout = InventoryGridLayout(
        SUPPORTED_PROFILE_ID, SUPPORTED_COLUMN_STRIDE, SUPPORTED_ROW_STRIDE,
    )
    slots = layout.all_slot_regions(Region(*SUPPORTED_REGION))
    count = 0
    saw_empty = False
    for index, (decision, slot) in enumerate(zip(guarded.slots, slots, strict=True)):
        if decision.index != index:
            return None
        if decision.raw_v1_state is SlotOccupancy.EMPTY and decision.raw_v1_confidence >= 0.8:
            saw_empty = True
        elif (
            decision.raw_v1_state is SlotOccupancy.OCCUPIED
            and not saw_empty
            and _positive_iron_slot(frame, slot)
        ):
            count += 1
        else:
            return None
    return count if count else None
