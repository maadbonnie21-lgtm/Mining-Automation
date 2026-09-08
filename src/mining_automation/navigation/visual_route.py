"""Image-registered navigation targets; no operating-system or input calls.

A waypoint is an observed place, represented by the minimap at arrival. The
reference centre is registered into the *current* minimap. Historical screen
clicks are never replayed. Profiles are development-only until validated live.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

MAP_SIZE = 192
MAP_CENTRE = (MAP_SIZE - 1) / 2


class LocalizationError(RuntimeError):
    """There is not enough current visual evidence to authorize a movement."""


@dataclass(frozen=True)
class MinimapGeometry:
    x: float
    y: float
    radius: float
    anchor_score: float

    def screen_point(self, point: tuple[float, float]) -> tuple[int, int]:
        factor = self.radius / MAP_CENTRE
        return (
            round(self.x + (point[0] - MAP_CENTRE) * factor),
            round(self.y + (point[1] - MAP_CENTRE) * factor),
        )

    def contains(self, point: tuple[int, int], inset: float = 0.20) -> bool:
        return math.hypot(point[0] - self.x, point[1] - self.y) < self.radius * (1 - inset)


@dataclass(frozen=True)
class Registration:
    target: tuple[float, float]
    distance: float
    inliers: int
    ratio: float
    error: float
    agreement: float
    scale: float
    rotation_degrees: float


@dataclass(frozen=True)
class StartConnector:
    """One evidence-bound outbound pose that must return to canonical start."""

    connector_id: str
    canonical_waypoint: str
    canonical_offset: tuple[float, float]
    offset_tolerance: float
    maximum_reach: float
    arrival_tolerance: float
    source_pose_id: str
    mining_terminal_frame_sha256: str
    route_observation_frame_sha256: str
    pose_reference_sha256: str


class MinimapLocator:
    """Locate recorded compass chrome in current pixels, including UI scale.

    The compact search area is a performance bound, not an absolute click
    location. Missing/ambiguous chrome produces zero movement authority.
    """

    def __init__(
        self,
        anchor: np.ndarray,
        mask: np.ndarray,
        minimap_offset: tuple[float, float],
        radius: float,
    ) -> None:
        self.anchor = anchor
        self.mask = mask
        self.offset = minimap_offset
        self.radius = radius

    def locate(self, frame: np.ndarray) -> MinimapGeometry:
        h, w = frame.shape[:2]
        left = max(0, w - 550)
        search = np.ascontiguousarray(frame[: min(h, 260), left:, :3])
        candidates: list[tuple[float, int, int, float]] = []
        for factor in np.arange(0.75, 1.56, 0.025):
            aw = round(self.anchor.shape[1] * factor)
            ah = round(self.anchor.shape[0] * factor)
            if aw >= search.shape[1] or ah >= search.shape[0]:
                continue
            anchor = cv2.resize(self.anchor, (aw, ah), interpolation=cv2.INTER_LINEAR)
            mask = cv2.resize(self.mask, (aw, ah), interpolation=cv2.INTER_NEAREST)
            scores = cv2.matchTemplate(search, anchor, cv2.TM_SQDIFF_NORMED, mask=mask)
            scores[~np.isfinite(scores)] = 10
            score, _, location, _ = cv2.minMaxLoc(scores)
            candidates.append((score, location[0] + left, location[1], float(factor)))
            # Also test a second spatial peak at this same scale.
            scores[
                max(0, location[1] - 10) : location[1] + 11,
                max(0, location[0] - 10) : location[0] + 11,
            ] = 10
            second_score, _, second_location, _ = cv2.minMaxLoc(scores)
            candidates.append(
                (second_score, second_location[0] + left, second_location[1], float(factor))
            )
        if not candidates:
            raise LocalizationError("minimap_chrome_missing")
        score, x, y, factor = min(candidates)
        if score > 0.05:
            raise LocalizationError(f"minimap_chrome_unproven: {score:.3f}")
        centre = (x + self.offset[0] * factor, y + self.offset[1] * factor)
        for other_score, ox, oy, ofactor in candidates:
            other = (ox + self.offset[0] * ofactor, oy + self.offset[1] * ofactor)
            if other_score <= score + 0.015 and math.dist(centre, other) > 10:
                raise LocalizationError("minimap_chrome_ambiguous")
        radius = self.radius * factor
        if (
            centre[0] - radius < 0
            or centre[0] + radius >= w
            or centre[1] - radius < 0
            or centre[1] + radius >= h
        ):
            raise LocalizationError("minimap_outside_frame")
        return MinimapGeometry(*centre, radius, score)


def crop_minimap(frame: np.ndarray, geometry: MinimapGeometry) -> np.ndarray:
    scale = geometry.radius / MAP_CENTRE
    matrix = np.array(
        [[scale, 0, geometry.x - geometry.radius], [0, scale, geometry.y - geometry.radius]],
        dtype=np.float32,
    )
    return cv2.warpAffine(
        frame[:, :, :3], matrix, (MAP_SIZE, MAP_SIZE), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP
    )


def terrain_mask(image: np.ndarray) -> np.ndarray:
    yy, xx = np.mgrid[: image.shape[0], : image.shape[1]]
    distance = np.hypot(xx - MAP_CENTRE, yy - MAP_CENTRE)
    mask = ((distance < MAP_CENTRE * 0.88) & (distance > 8)).astype(np.uint8) * 255
    b, g, r = cv2.split(image[:, :, :3])
    # Moving yellow NPC markers are not fixed landmarks. The player centre is
    # also excluded. Fixed terrain/POI geometry must support registration.
    yellow = ((r > 185) & (g > 165) & (b < 125)).astype(np.uint8) * 255
    expanded_yellow = cv2.dilate(yellow, np.ones((5, 5), np.uint8))
    mask[expanded_yellow != 0] = 0
    return np.asarray(mask, dtype=np.uint8)


class TerrainReference:
    def __init__(self, image: np.ndarray) -> None:
        if image.shape != (MAP_SIZE, MAP_SIZE, 3):
            raise ValueError("Expected canonical 192x192 BGR minimap")
        self.image = image
        self.mask = terrain_mask(image)
        self.detector = cv2.SIFT.create(nfeatures=1200, contrastThreshold=0.015, edgeThreshold=12)
        self.keypoints, self.descriptors = self._features(image, self.mask)

    def _features(self, image: np.ndarray, mask: np.ndarray) -> tuple[Any, Any]:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        mask = cv2.resize(mask, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
        keypoints, descriptors = self.detector.detectAndCompute(gray, mask)
        return keypoints, descriptors

    def register(self, current: np.ndarray) -> Registration:
        mask = terrain_mask(current)
        keypoints, descriptors = self._features(current, mask)
        if self.descriptors is None or descriptors is None:
            raise LocalizationError("terrain_features_missing")
        pairs = cv2.BFMatcher().knnMatch(self.descriptors, descriptors, k=2)
        good = [
            pair[0]
            for pair in pairs
            if len(pair) == 2 and pair[0].distance < 0.70 * pair[1].distance
        ]
        if len(good) < 8:
            raise LocalizationError(f"terrain_matches_insufficient: {len(good)}")
        source = np.asarray([self.keypoints[m.queryIdx].pt for m in good], dtype=np.float32) / 2
        dest = np.asarray([keypoints[m.trainIdx].pt for m in good], dtype=np.float32) / 2
        cv2.setRNGSeed(0)
        matrix, flags = cv2.estimateAffinePartial2D(
            source,
            dest,
            method=cv2.RANSAC,
            ransacReprojThreshold=1.8,
            maxIters=2500,
            confidence=0.995,
        )
        if matrix is None or flags is None or not np.isfinite(matrix).all():
            raise LocalizationError("terrain_transform_unproven")
        accepted = flags.ravel().astype(bool)
        count = int(accepted.sum())
        ratio = count / len(good)
        span = np.ptp(source[accepted], axis=0) if count else np.zeros(2)
        unique = len(np.unique(np.round(source[accepted] / 4), axis=0))
        if count < 8 or unique < 6 or ratio < 0.45 or min(span) < 20:
            raise LocalizationError(f"terrain_consensus_weak: {count}/{len(good)}, spread={span}")
        scale = math.hypot(matrix[0, 0], matrix[1, 0])
        rotation = math.degrees(math.atan2(matrix[1, 0], matrix[0, 0]))
        if not 0.80 <= scale <= 1.25 or abs(rotation) > 8:
            raise LocalizationError("terrain_scale_or_orientation_unsupported")
        transformed = cv2.transform(source.reshape(-1, 1, 2), matrix).reshape(-1, 2)
        error = float(np.median(np.linalg.norm(transformed[accepted] - dest[accepted], axis=1)))
        warped = cv2.warpAffine(self.image, matrix, (MAP_SIZE, MAP_SIZE))
        warped_mask = cv2.warpAffine(
            self.mask, matrix, (MAP_SIZE, MAP_SIZE), flags=cv2.INTER_NEAREST
        )
        overlap = (warped_mask > 0) & (mask > 0)
        if int(overlap.sum()) < 3000:
            raise LocalizationError("terrain_overlap_insufficient")
        diff = np.max(np.abs(warped.astype(np.int16) - current.astype(np.int16)), axis=2)
        agreement = float(np.mean(diff[overlap] < 35))
        if error > 1.4 or agreement < 0.62:
            raise LocalizationError(
                f"terrain_pixel_verification_failed: error={error:.2f}, agreement={agreement:.2f}"
            )
        target_array = matrix @ np.array([MAP_CENTRE, MAP_CENTRE, 1.0])
        target = (float(target_array[0]), float(target_array[1]))
        return Registration(
            target,
            math.dist(target, (MAP_CENTRE, MAP_CENTRE)),
            count,
            ratio,
            error,
            agreement,
            scale,
            rotation,
        )


@dataclass(frozen=True)
class Waypoint:
    name: str
    image_key: str
    tolerance: float = 3.0


class VisualRoute:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.config = json.loads(self.path.read_text(encoding="utf-8"))
        if self.config.get("schema_version") != 1:
            raise ValueError("Unsupported route schema")
        asset = (self.path.parent / self.config["assets"]).resolve()
        if not asset.is_relative_to(self.path.parent):
            raise ValueError("Route asset must stay inside the profile directory")
        if hashlib.sha256(asset.read_bytes()).hexdigest() != self.config.get("assets_sha256"):
            raise ValueError("Route asset hash mismatch")
        with np.load(asset, allow_pickle=False) as archive:
            self.images = {key: archive[key].copy() for key in archive.files}
        self.locator = MinimapLocator(
            self.images["chrome"],
            self.images["chrome_mask"],
            tuple(self.config["minimap_offset"]),
            self.config["minimap_radius"],
        )
        self.waypoints = [Waypoint(**item) for item in self.config["waypoints"]]
        if any(not math.isfinite(w.tolerance) or not 1 <= w.tolerance <= 4 for w in self.waypoints):
            raise ValueError("Waypoint arrival tolerance outside reviewed range")
        if not self.waypoints or len({w.name for w in self.waypoints}) != len(self.waypoints):
            raise ValueError("Empty route or duplicate waypoint identifiers")
        self.outbound_start_name = self.waypoints[0].name
        self.start_connectors: list[StartConnector] = []
        for item in self.config.get("start_connectors", []):
            raw_offset = item.get("canonical_offset")
            if (
                not isinstance(raw_offset, list)
                or len(raw_offset) != 2
                or any(
                    isinstance(value, bool) or not isinstance(value, (int, float))
                    for value in raw_offset
                )
            ):
                raise ValueError("Start connector offset must contain two numbers")
            connector = StartConnector(
                connector_id=item["connector_id"],
                canonical_waypoint=item["canonical_waypoint"],
                canonical_offset=(float(raw_offset[0]), float(raw_offset[1])),
                offset_tolerance=float(item["offset_tolerance"]),
                maximum_reach=float(item["maximum_reach"]),
                arrival_tolerance=float(item["arrival_tolerance"]),
                source_pose_id=item["source_pose_id"],
                mining_terminal_frame_sha256=item["mining_terminal_frame_sha256"],
                route_observation_frame_sha256=item["route_observation_frame_sha256"],
                pose_reference_sha256=item["pose_reference_sha256"],
            )
            hashes = (
                connector.mining_terminal_frame_sha256,
                connector.route_observation_frame_sha256,
                connector.pose_reference_sha256,
            )
            if (
                not connector.connector_id
                or connector.canonical_waypoint != self.outbound_start_name
                or not connector.source_pose_id
                or any(not math.isfinite(value) for value in connector.canonical_offset)
                or not 0 < connector.offset_tolerance <= 1.0
                or not 0 < connector.maximum_reach <= 12.0
                or not 0 < connector.arrival_tolerance <= self.waypoints[0].tolerance
                or math.hypot(*connector.canonical_offset) <= self.waypoints[0].tolerance
                or math.hypot(*connector.canonical_offset) > connector.maximum_reach
                or any(
                    len(value) != 64
                    or value.lower() != value
                    or any(character not in "0123456789abcdef" for character in value)
                    for value in hashes
                )
            ):
                raise ValueError("Start connector is outside the reviewed safety envelope")
            self.start_connectors.append(connector)
        if len({connector.connector_id for connector in self.start_connectors}) != len(
            self.start_connectors
        ):
            raise ValueError("Duplicate start connector identifiers")
        self.references = {
            key: TerrainReference(value)
            for key, value in self.images.items()
            if key.startswith("map_")
        }

    def match_start_connector(
        self, waypoint: Waypoint, registration: Registration
    ) -> dict[str, Any] | None:
        """Match only a tightly bound outbound departure vector.

        The stored vector is canonical terrain geometry, not a historical
        screen click. Apply the freshly measured registration scale/rotation
        before comparing it with the live target vector.
        """

        if waypoint.name != self.outbound_start_name:
            return None
        observed = (
            registration.target[0] - MAP_CENTRE,
            registration.target[1] - MAP_CENTRE,
        )
        angle = math.radians(registration.rotation_degrees)
        cosine, sine = math.cos(angle), math.sin(angle)
        matches: list[dict[str, Any]] = []
        for connector in self.start_connectors:
            dx, dy = connector.canonical_offset
            expected = (
                registration.scale * (cosine * dx - sine * dy),
                registration.scale * (sine * dx + cosine * dy),
            )
            residual = math.dist(observed, expected)
            if (
                registration.distance <= connector.maximum_reach
                and residual <= connector.offset_tolerance
            ):
                matches.append(
                    {
                        "connector_id": connector.connector_id,
                        "canonical_waypoint": connector.canonical_waypoint,
                        "source_pose_id": connector.source_pose_id,
                        "canonical_offset": list(connector.canonical_offset),
                        "expected_live_offset": list(expected),
                        "observed_live_offset": list(observed),
                        "offset_residual": residual,
                        "offset_tolerance": connector.offset_tolerance,
                        "maximum_reach": connector.maximum_reach,
                        "arrival_tolerance": connector.arrival_tolerance,
                        "mining_terminal_frame_sha256": connector.mining_terminal_frame_sha256,
                        "route_observation_frame_sha256": connector.route_observation_frame_sha256,
                        "pose_reference_sha256": connector.pose_reference_sha256,
                    }
                )
        if len(matches) > 1:
            raise LocalizationError("start_connector_ambiguous")
        return matches[0] if matches else None

    def observe(
        self, image: np.ndarray, waypoint: Waypoint
    ) -> tuple[MinimapGeometry, Registration]:
        geometry = self.locator.locate(image)
        # A valid minimap locator plus expected-route terrain registration is
        # the gameplay gate. Login/disconnect screens fail the locator and/or
        # terrain registration; do not bind route authority to dynamic orb text.
        registration = self.references[waypoint.image_key].register(crop_minimap(image, geometry))
        return geometry, registration

    def observe_at_geometry(
        self,
        image: np.ndarray,
        waypoint: Waypoint,
        geometry: MinimapGeometry,
    ) -> tuple[MinimapGeometry, Registration]:
        """Re-register fresh terrain at an immediately prior, window-bound map geometry.

        This narrow path is used only after the outbound connector's native
        Resource+Inventory authority capture. No movement occurred between the
        two frames, and the runtime still requires identical window geometry,
        fresh terrain consensus, the same connector, and a current health proof.
        """

        if not self.verify_gameplay(image, geometry):
            raise LocalizationError("gameplay_chrome_unproven")
        registration = self.references[waypoint.image_key].register(
            crop_minimap(image, geometry)
        )
        return geometry, registration

    localization_error = LocalizationError
    centre = (MAP_CENTRE, MAP_CENTRE)

    def verify_gameplay(self, image: np.ndarray, geometry: MinimapGeometry) -> bool:
        """Require fixed health/prayer orb chrome, independent of terrain matching."""
        box = self.config["gameplay_chrome_box_relative"]
        x1, y1, x2, y2 = [
            round((geometry.x if i % 2 == 0 else geometry.y) + v * geometry.radius)
            for i, v in enumerate(box)
        ]
        if min(x1, y1) < 0 or x2 > image.shape[1] or y2 > image.shape[0] or x2 <= x1 or y2 <= y1:
            return False
        expected = self.images["gameplay_chrome"]
        actual = cv2.resize(image[y1:y2, x1:x2, :3], (expected.shape[1], expected.shape[0]))
        value = float(cv2.matchTemplate(actual, expected, cv2.TM_CCOEFF_NORMED)[0, 0])
        return math.isfinite(value) and value >= 0.88

    def verify_endpoint(self, image: np.ndarray, geometry: MinimapGeometry) -> dict[str, Any]:
        """Require a proven static counter appearance at the registered endpoint."""
        names = ["counter", *self.config.get("counter_additional_templates", [])]
        scale = geometry.radius / self.config["counter_reference_radius"]
        height = round(image.shape[0] * 0.84)
        width = round(image.shape[1] * 0.66)
        world = image[32:height, :width, :3]
        best = -1.0
        matched_name = None
        for name in names:
            template = self.images[name]
            for factor in (scale * 0.95, scale, scale * 1.05):
                tw, th = round(template.shape[1] * factor), round(template.shape[0] * factor)
                if tw < 8 or th < 8 or tw >= width or th >= world.shape[0]:
                    continue
                patch = cv2.resize(template, (tw, th))
                scores = cv2.matchTemplate(world, patch, cv2.TM_CCOEFF_NORMED)
                scores[~np.isfinite(scores)] = -1
                score = float(cv2.minMaxLoc(scores)[1])
                if score > best:
                    best, matched_name = score, name
        return {
            "accepted": best >= 0.83,
            "counter_score": best,
            "counter_template": matched_name,
            "bank_interface_opened": False,
            "authority": "arrival_only",
        }

    def verify_health(self, image: np.ndarray, geometry: MinimapGeometry) -> bool:
        """Recognize the reviewed 23-HP display with small UI-location tolerance."""
        box = self.config["healthy_display_box_relative"]
        x1, y1, x2, y2 = [
            round((geometry.x if i % 2 == 0 else geometry.y) + v * geometry.radius)
            for i, v in enumerate(box)
        ]
        pad = 8
        x1p, y1p = max(0, x1 - pad), max(0, y1 - pad)
        x2p, y2p = min(image.shape[1], x2 + pad), min(image.shape[0], y2 + pad)
        roi = image[y1p:y2p, x1p:x2p, :3]
        expected = self.images["healthy_display"]
        if roi.shape[0] < expected.shape[0] or roi.shape[1] < expected.shape[1]:
            return False
        scores = cv2.matchTemplate(roi, expected, cv2.TM_CCOEFF_NORMED)
        scores[~np.isfinite(scores)] = -1
        return float(cv2.minMaxLoc(scores)[1]) >= 0.68
