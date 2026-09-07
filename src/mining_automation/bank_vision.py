"""Evidence-derived iron-only banking perception; no input or window operations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


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
    return (np.max(image[:, :, :3], axis=2) > 170).astype(np.uint8) * 255


class BankVision:
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

    def inventory_origin(self, image: Any) -> tuple[int, int]:
        edge = self.match(
            image,
            "inventory_edge",
            (image.shape[1] - 100, image.shape[0] - 340, image.shape[1], image.shape[0]),
        )
        if edge.score < 0.80:
            raise BankUnproven("inventory_border_unproven:" + str(edge.score))
        return edge.x - 193, edge.y + 1

    def inventory(self, image: Any, origin: tuple[int, int] | None = None) -> dict[str, Any]:
        left, top = origin or self.inventory_origin(image)
        ores = []
        empties = []
        unknown = []
        for row in range(7):
            for col in range(4):
                x, y = left + 13 + col * 42, top + 3 + row * 36
                patch = image[y : y + 34, x : x + 40, :3]
                expected = self.assets["ore"]
                scores = cv2.matchTemplate(patch, expected, cv2.TM_CCOEFF_NORMED)
                score = float(cv2.minMaxLoc(scores)[1])
                slot = (x, y, x + 40, y + 34)
                if score > 0.85:
                    ores.append(slot)
                    continue
                ex = self.assets["empty_open"][
                    3 + row * 36 : 37 + row * 36, 13 + col * 42 : 53 + col * 42
                ]
                delta = np.max(np.abs(patch.astype(np.int16) - ex.astype(np.int16)), axis=2)
                if float(np.mean(delta < 22)) > 0.93:
                    empties.append(slot)
                else:
                    unknown.append(slot)
        return {
            "ore_count": len(ores),
            "empty_count": len(empties),
            "unknown_count": len(unknown),
            "ore_slots": ores,
            "origin": [left, top],
        }
