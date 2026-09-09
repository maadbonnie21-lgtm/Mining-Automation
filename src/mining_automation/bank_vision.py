"""Evidence-derived iron-only banking perception; no input or window operations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .perception.inventory.retained_iron import (
    SOURCE_VERIFIED_MINING_BYPRODUCT_IDS,
    source_verified_mining_byproduct,
)

_DEPOSIT_ALL_PREFIX_MASK_HEX = (
    "01f800000000000000000000078000000000000000000007f8000000000000000000007f800000"
    "00000000e1980007780000000018001b1980007700000000018003199800077000000001980031998"
    "000771e0f0e0f81e003f99800077331999981987e31998000777319998f1980031998000777e199881"
    "9980031998000773019998199800319980007e1f1f0f1f18e003199800020001800000000000000000"
    "000018000000000000000000000180000000000000000000001800000000000000000000000000000"
    "0000000000"
)
_DEPOSIT_ALL_PREFIX_SHAPE = (18, 92)
_BANK_LOGICAL_UNCUT_RUBY_SLOT_RGB_SHA256 = (
    "52f4b427fadbaec40936200ec8a5a51fa4f264f0f6f75fbb5647d77c6d6e919e"
)
_BANK_LOGICAL_UNCUT_RUBY_SPRITE_SIGNATURE_SHA256 = (
    "ce17d36306110a3d5fbcaa75a4d42dfacd0702199b1a68e799d19b6f6c6112cd"
)

_DEPOSIT_ALL_PREFIX_SOURCE_SHA256 = (
    "8dcaf2733824e7904c0b282b83180f14df7a64b0f0f82468f2dac8c6a0b7fa14"
)


class BankUnproven(RuntimeError):
    pass


@dataclass(frozen=True)
class Match:
    x: int
    y: int
    width: int
    height: int
    score: float

    @property
    def centre(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


def glyphs(image: Any) -> Any:
    b, g, r = cv2.split(image[:, :, :3])
    white = np.minimum(np.minimum(b, g), r) > 200
    cyan = (b > 140) & (g > 140) & (r < 120)
    orange = (r > 180) & (g > 60) & (b < 80)
    yellow = (r > 180) & (g > 180) & (b < 100)
    return (white | cyan | orange | yellow).astype(np.uint8) * 255


class BankVision:
    deposit_all_prefix_source_sha256 = _DEPOSIT_ALL_PREFIX_SOURCE_SHA256

    def __init__(self) -> None:
        folder = Path(__file__).parent / "bank_profiles"
        asset = folder / "iron_bank.npz"
        meta = json.loads((folder / "iron_bank.json").read_text())
        if hashlib.sha256(asset.read_bytes()).hexdigest() != meta["assets_sha256"]:
            raise BankUnproven("bank_asset_digest_mismatch")
        with np.load(asset, allow_pickle=False) as archive:
            self.assets = {key: archive[key].copy() for key in archive.files}

    def match(
        self, image: Any, key: str, box: tuple[int, int, int, int] | None = None, text: bool = False
    ) -> Match:
        x1, y1, x2, y2 = box or (0, 0, image.shape[1], image.shape[0])
        roi = image[y1:y2, x1:x2, :3]
        template = self.assets[key]
        if text:
            roi, template = glyphs(roi), glyphs(template)
        if roi.shape[0] < template.shape[0] or roi.shape[1] < template.shape[1]:
            raise BankUnproven("search_region_too_small:" + key)
        scores = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
        scores[~np.isfinite(scores)] = -1
        _, score, _, (x, y) = cv2.minMaxLoc(scores)
        return Match(x + x1, y + y1, template.shape[1], template.shape[0], float(score))

    def bank_controls(self, image: Any) -> tuple[Match, Match] | None:
        title = self.match(image, "bank_title", (0, 20, 520, 130))
        if title.score < 0.85:
            return None
        close = self.match(
            image,
            "bank_x",
            (
                max(0, title.x + 260),
                max(20, title.y - 15),
                min(image.shape[1], title.x + 345),
                title.y + 35,
            ),
        )
        return (title, close) if close.score >= 0.85 else None

    def bank_booth_hover_text(self, image: Any) -> bool:
        """Recognize the Bank booth action text in the logical 804px presentation."""
        logical = image
        if image.shape[1] != 804:
            scale = 804 / image.shape[1]
            logical = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        roi = logical[24:48, 0:264, :3]
        if roi.shape[0] != 24 or roi.shape[1] != 264:
            return False
        blue, green, red = cv2.split(roi)
        cyan = (blue > 120) & (green > 120) & (red < 120) & (
            blue.astype(np.int16) + green.astype(np.int16) - 2 * red.astype(np.int16) > 120
        )
        white = (blue > 170) & (green > 170) & (red > 170)
        cyan_count = int(cyan.sum())
        white_count = int(white.sum())
        if not 180 <= cyan_count <= 350 or not 250 <= white_count <= 500:
            return False
        ys, xs = np.where(cyan)
        if len(xs) == 0:
            return False
        left, top = int(xs.min()), int(ys.min()) + 24
        right, bottom = int(xs.max()), int(ys.max()) + 24
        return 40 <= left <= 52 and 112 <= right <= 128 and 30 <= top <= 35 and 39 <= bottom <= 44

    def inventory_origin(self, image: Any) -> tuple[int, int]:
        edge = self.match(
            image,
            "inventory_edge",
            (image.shape[1] - 100, image.shape[0] - 340, image.shape[1], image.shape[0]),
        )
        if edge.score < 0.80:
            raise BankUnproven("inventory_border_unproven:" + str(edge.score))
        return edge.x - 193, edge.y + 1

    @staticmethod
    def _logical_bank_byproduct(*, full_slot_rgb: bytes, slot_index: int) -> dict[str, Any] | None:
        """Recognize the source-verified ruby after the fixed bank-view resize."""
        if type(full_slot_rgb) is not bytes or len(full_slot_rgb) != 32 * 32 * 3:
            return None
        if type(slot_index) is not int or not 0 <= slot_index < 28:
            return None
        signature = bytearray()
        for y in range(32):
            for x in range(32):
                offset = (y * 32 + x) * 3
                red, green, blue = full_slot_rgb[offset : offset + 3]
                if red > 60 and red - green > 30 and red - blue > 35:
                    signature.extend((x, y, red, green, blue))
        if (
            hashlib.sha256(signature).hexdigest()
            != _BANK_LOGICAL_UNCUT_RUBY_SPRITE_SIGNATURE_SHA256
        ):
            return None
        return {
            "item_id": "uncut_ruby",
            "display_name": "Uncut ruby",
            "slot_index": slot_index,
            "presentation": "bank_logical_resampled",
            "presentation_slot_rgb_sha256": hashlib.sha256(full_slot_rgb).hexdigest(),
            "source_slot_rgb_sha256": _BANK_LOGICAL_UNCUT_RUBY_SLOT_RGB_SHA256,
        }

    def inventory(self, image: Any, origin: tuple[int, int] | None = None) -> dict[str, Any]:
        left, top = origin or self.inventory_origin(image)
        ores = []
        gems = []
        empties = []
        unknown = []
        for row in range(7):
            for col in range(4):
                index = row * 4 + col
                x, y = left + 13 + col * 42, top + 3 + row * 36
                patch = image[y : y + 34, x : x + 40, :3]
                expected_empty = self.assets["empty_open"][
                    3 + row * 36 : 37 + row * 36,
                    13 + col * 42 : 53 + col * 42,
                ]
                # The 40px search patch includes four left-margin pixels;
                # align the shared 32px native item signature, not the patch centre.
                full_slot_rgb = patch[2:34, 4:36, :3][:, :, ::-1].tobytes()
                slot_pixels = patch[2:34, 4:36, :3]
                expected_slot_pixels = expected_empty[2:34, 4:36, :3]
                perimeter = np.ones((32, 32), dtype=bool)
                perimeter[3:29, 3:29] = False
                background_delta = np.max(
                    np.abs(slot_pixels.astype(np.int16) - expected_slot_pixels.astype(np.int16)),
                    axis=2,
                )
                background_proven = float(np.mean(background_delta[perimeter] < 22)) > 0.93
                byproduct = (
                    source_verified_mining_byproduct(
                        full_slot_rgb=full_slot_rgb,
                        slot_index=index,
                        allowed_byproducts=SOURCE_VERIFIED_MINING_BYPRODUCT_IDS,
                    )
                    if background_proven
                    else None
                )
                logical_byproduct = (
                    self._logical_bank_byproduct(full_slot_rgb=full_slot_rgb, slot_index=index)
                    if background_proven and byproduct is None
                    else None
                )
                slot = (x, y, x + 40, y + 34)
                if byproduct is not None:
                    gems.append({"slot": slot, **asdict(byproduct)})
                    continue
                if logical_byproduct is not None:
                    gems.append({"slot": slot, **logical_byproduct})
                    continue
                expected = self.assets["ore"]
                scores = cv2.matchTemplate(patch, expected, cv2.TM_CCOEFF_NORMED)
                score = float(cv2.minMaxLoc(scores)[1])
                if score > 0.85:
                    ores.append(slot)
                    continue
                delta = np.max(
                    np.abs(patch.astype(np.int16) - expected_empty.astype(np.int16)),
                    axis=2,
                )
                if float(np.mean(delta < 22)) > 0.93:
                    empties.append(slot)
                else:
                    unknown.append(slot)
        return {
            "ore_count": len(ores),
            "gem_count": len(gems),
            "gems": gems,
            "gem_slots": [item["slot"] for item in gems],
            "empty_count": len(empties),
            "unknown_count": len(unknown),
            "occupied_count": len(ores) + len(gems) + len(unknown),
            "ore_slots": ores,
            "origin": [left, top],
        }

    @staticmethod
    def deposit_all_prefix_score(image: Any) -> float:
        """Match the frozen live ``Deposit-All`` action prefix only.

        Item identity is proved independently from the freshly guarded slot.
        The shared prefix proves that Quantity-All would act on that hovered
        item; it does not infer or OCR an unfamiliar suffix.
        """

        logical = image
        if image.shape[1] != 804:
            scale = 804 / image.shape[1]
            logical = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        action_roi = logical[24:48, 0:100, :3]
        if action_roi.shape[:2] == (24, 100):
            low = np.min(action_roi, axis=2)
            high = np.max(action_roi, axis=2)
            white = (low > 120) & ((high - low) < 55)
            ys, xs = np.where(white)
            if len(xs):
                count = int(white.sum())
                left, top = int(xs.min()), int(ys.min()) + 24
                right, bottom = int(xs.max()), int(ys.max()) + 24
                if (
                    220 <= count <= 320
                    and 7 <= left <= 12
                    and 80 <= right <= 90
                    and 31 <= top <= 34
                    and 44 <= bottom <= 47
                ):
                    return 1.0

        height, width = _DEPOSIT_ALL_PREFIX_SHAPE
        template = (
            np.unpackbits(
                np.frombuffer(bytes.fromhex(_DEPOSIT_ALL_PREFIX_MASK_HEX), dtype=np.uint8),
                bitorder="big",
                count=height * width,
            )
            .reshape((height, width))
            .astype(bool)
        )
        best = 0.0
        for y in range(28, 31):
            for x in range(2, 5):
                crop = image[y : y + height, x : x + width, :3]
                if crop.shape[:2] != (height, width):
                    continue
                low = np.min(crop, axis=2)
                high = np.max(crop, axis=2)
                actual = (low > 120) & ((high - low) < 55)
                union = int(np.count_nonzero(template | actual))
                if union:
                    best = max(
                        best,
                        float(np.count_nonzero(template & actual)) / union,
                    )
        return best
