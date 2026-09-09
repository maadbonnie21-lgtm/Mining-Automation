"""Live item-specific mining-load deposit followed by verified bank-X close."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2

from .bank_vision import BankUnproven, BankVision
from .navigation.windows import NativeRouteBackend


@dataclass(frozen=True)
class BankRegion:
    left: int
    top: int
    right: int
    bottom: int

    def contains(self, point: tuple[int, int]) -> bool:
        return self.left <= point[0] < self.right and self.top <= point[1] < self.bottom


class BankRunner:
    def __init__(self, backend: NativeRouteBackend) -> None:
        self.backend = backend
        self.vision = BankVision()
        dpi = backend.initial["dpi_environment"]
        self.sx = dpi["effective_mapping_scale_x"]
        self.sy = dpi["effective_mapping_scale_y"]
        self.events: list[dict[str, Any]] = []

    def record(self, state: str, **facts: Any) -> None:
        event = {"state": state, "time": time.time(), **facts}
        self.events.append(event)
        print(json.dumps(event), flush=True)
        with (self.backend.output / "bank-events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    def observe(self, label: str) -> tuple[Any, Any]:
        frame = self.backend.capture(label)
        image = cv2.resize(
            frame.image, None, fx=1 / self.sx, fy=1 / self.sy, interpolation=cv2.INTER_AREA
        )
        return frame, image

    def physical(self, point: tuple[int, int]) -> tuple[int, int]:
        return round(point[0] * self.sx), round(point[1] * self.sy)

    def hover(self, point: tuple[int, int]) -> None:
        state = self.backend.guard()
        x, y = self.physical(point)
        ox, oy = state["client_origin"]
        if not self.backend.api.move_cursor(ox + x, oy + y):
            raise BankUnproven("hover_failed")
        self.backend.wait(0.20)

    def click(self, frame: Any, point: tuple[int, int], action: str) -> None:
        x, y = self.physical(point)
        self.record("ACTION", action=action, frame_id=frame.frame_id, physical_point=[x, y])
        self.backend.click(frame, (x, y), BankRegion(x - 3, y - 3, x + 4, y + 4))

    def wait_open(self) -> tuple[Any, Any]:
        for _ in range(15):
            frame, image = self.observe("verify-bank-open")
            if self.vision.bank_controls(image):
                self.record("BANK_OPEN_VERIFIED", frame_id=frame.frame_id)
                return frame, image
            self.backend.wait(0.25)
        raise BankUnproven("bank_did_not_open")

    @staticmethod
    def _gem_ids(inventory: dict[str, Any]) -> list[str]:
        return [str(item["item_id"]) for item in inventory["gems"]]

    @staticmethod
    def _item_slots(inventory: dict[str, Any], item_id: str) -> list[tuple[int, int, int, int]]:
        if item_id == "iron_ore":
            return [tuple(slot) for slot in inventory["ore_slots"]]
        if item_id == "uncut_ruby":
            return [
                tuple(item["slot"]) for item in inventory["gems"] if item["item_id"] == "uncut_ruby"
            ]
        raise BankUnproven("unsupported_deposit_item:" + item_id)

    @classmethod
    def _known_composition(
        cls,
        inventory: dict[str, Any],
        *,
        ore_count: int,
        gem_ids: list[str],
        empty_count: int,
    ) -> bool:
        return (
            inventory["ore_count"] == ore_count
            and cls._gem_ids(inventory) == gem_ids
            and inventory["gem_count"] == len(gem_ids)
            and inventory["empty_count"] == empty_count
            and inventory["unknown_count"] == 0
            and inventory["occupied_count"] == ore_count + len(gem_ids)
        )

    def _wait_composition(
        self,
        *,
        label: str,
        ore_count: int,
        gem_ids: list[str],
        empty_count: int,
    ) -> tuple[Any, Any, dict[str, Any]]:
        proofs = 0
        last: tuple[Any, Any, dict[str, Any]] | None = None
        for _ in range(15):
            frame, image = self.observe(label)
            if not self.vision.bank_controls(image):
                raise BankUnproven("bank_not_open_during_deposit_verification")
            inventory = self.vision.inventory(image)
            last = (frame, image, inventory)
            if self._known_composition(
                inventory,
                ore_count=ore_count,
                gem_ids=gem_ids,
                empty_count=empty_count,
            ):
                proofs += 1
                if proofs >= 2:
                    return last
            else:
                proofs = 0
            self.backend.wait(0.25)
        raise BankUnproven("item_specific_deposit_not_verified")

    def _deposit_all_item(
        self,
        *,
        frame: Any,
        image: Any,
        inventory: dict[str, Any],
        item_id: str,
        expected_ore_before: int,
        expected_gems_before: list[str],
        expected_ore_after: int,
        expected_gems_after: list[str],
    ) -> tuple[Any, Any, dict[str, Any]]:
        slots = self._item_slots(inventory, item_id)
        if not slots:
            raise BankUnproven("approved_deposit_item_missing:" + item_id)
        point = ((slots[0][0] + slots[0][2]) // 2, (slots[0][1] + slots[0][3]) // 2)
        self.hover(point)
        frame, image = self.observe(f"{item_id}-deposit-all-hover")
        hovered = self.vision.inventory(image)
        hover_slots = self._item_slots(hovered, item_id)
        prefix_score = self.vision.deposit_all_prefix_score(image)
        self.record(
            "ITEM_SPECIFIC_DEPOSIT_HOVER",
            item_id=item_id,
            prefix_score=prefix_score,
            frame_id=frame.frame_id,
            source_prefix_sha256=self.vision.deposit_all_prefix_source_sha256,
        )
        if point not in [
            ((slot[0] + slot[2]) // 2, (slot[1] + slot[3]) // 2) for slot in hover_slots
        ]:
            raise BankUnproven("hovered_item_identity_changed:" + item_id)
        if prefix_score < 0.85:
            raise BankUnproven("deposit_all_hover_unproven:" + item_id)

        # Clear the top action overlay and recapture. The historical hover point
        # is never clicked: the item is found again in this newer clean frame.
        self.hover((100, 15))
        frame, image = self.observe(f"pre-{item_id}-deposit-clean")
        controls = self.vision.bank_controls(image)
        if controls is None:
            raise BankUnproven("bank_closed_before_deposit")
        title = controls[0]
        all_box = (title.x + 70, title.y + 600, title.x + 145, title.y + 660)
        selected = self.vision.match(image, "quantity_all_selected", all_box)
        self.record("QUANTITY_ALL_VERIFIED", item_id=item_id, score=selected.score)
        if selected.score < 0.90:
            raise BankUnproven("quantity_All_not_selected")
        inventory = self.vision.inventory(image)
        if not self._known_composition(
            inventory,
            ore_count=expected_ore_before,
            gem_ids=expected_gems_before,
            empty_count=28 - expected_ore_before - len(expected_gems_before),
        ):
            raise BankUnproven("inventory_changed_before_deposit:" + item_id)
        fresh_slots = self._item_slots(inventory, item_id)
        if not fresh_slots:
            raise BankUnproven("fresh_deposit_item_missing:" + item_id)
        fresh_point = (
            (fresh_slots[0][0] + fresh_slots[0][2]) // 2,
            (fresh_slots[0][1] + fresh_slots[0][3]) // 2,
        )
        action = (
            "DEPOSIT_ALL_IRON_ORE_ONLY" if item_id == "iron_ore" else "DEPOSIT_ALL_UNCUT_RUBY_ONLY"
        )
        self.click(frame, fresh_point, action)
        self.hover((100, 15))
        return self._wait_composition(
            label=f"verify-{item_id}-deposit",
            ore_count=expected_ore_after,
            gem_ids=expected_gems_after,
            empty_count=28 - expected_ore_after - len(expected_gems_after),
        )

    def run(self, open_only: bool = False) -> dict[str, Any]:
        self.hover((100, 15))
        frame, image = self.observe("banking-start")
        inventory = self.vision.inventory(image)
        self.record("INVENTORY_BEFORE", **inventory)
        before_ore_count = inventory["ore_count"]
        before_gem_ids = self._gem_ids(inventory)
        if (
            inventory["occupied_count"] != 28
            or inventory["unknown_count"]
            or before_ore_count + len(before_gem_ids) != 28
        ):
            raise BankUnproven("requires_verified_full_mining_load")
        if not self.vision.bank_controls(image):
            booth = self.vision.match(image, "booth", (0, 100, 530, image.shape[0] - 150))
            if booth.score < 0.55:
                raise BankUnproven("bank_booth_unproven:" + str(booth.score))
            self.hover(booth.centre)
            frame, image = self.observe("bank-booth-hover")
            proof = max(
                (
                    self.vision.match(image, key, (0, 24, 330, 60), text=True)
                    for key in ("bank_hover", "bank_hover_original", "bank_hover_live2", "bank_hover_live3", "bank_hover_live4")
                ),
                key=lambda match: match.score,
            )
            # Preserve the original >=0.94 gold-booth authority. A farther-out
            # presentation may use the same frozen booth target down to 0.65 only
            # when a fresh independent Bank-booth hover-text template reaches 0.85.
            semantic_hover = self.vision.bank_booth_hover_text(image)
            if booth.score < 0.94 and proof.score < 0.85 and not semantic_hover:
                raise BankUnproven(
                    "bank_booth_hover_unproven:" + str(booth.score) + ":" + str(proof.score)
                )
            self.record(
                "BOOTH_TARGET_VERIFIED",
                appearance_score=booth.score,
                hover_text_score=proof.score,
                semantic_hover_text=semantic_hover,
            )
            self.click(frame, booth.centre, "OPEN_BANK_BOOTH")
            frame, image = self.wait_open()
        if open_only:
            return {"status": "OPEN_STAGE_PASS", "success": False, "deposit_verified": False}
        inventory = self.vision.inventory(image)
        if not self._known_composition(
            inventory,
            ore_count=before_ore_count,
            gem_ids=before_gem_ids,
            empty_count=0,
        ):
            raise BankUnproven("bank_inventory_is_not_same_full_mining_load")
        controls = self.vision.bank_controls(image)
        if controls is None:
            raise BankUnproven("bank_controls_lost")
        title = controls[0]
        all_box = (title.x + 70, title.y + 600, title.x + 145, title.y + 660)
        active = self.vision.match(image, "quantity_all_selected", all_box)
        if active.score < 0.90:
            all_button = self.vision.match(image, "quantity_all", all_box)
            if all_button.score < 0.83:
                raise BankUnproven("quantity_All_control_unproven")
            self.click(frame, all_button.centre, "SET_ITEM_QUANTITY_ALL")
        if before_ore_count:
            frame, image, inventory = self._deposit_all_item(
                frame=frame,
                image=image,
                inventory=inventory,
                item_id="iron_ore",
                expected_ore_before=before_ore_count,
                expected_gems_before=before_gem_ids,
                expected_ore_after=0,
                expected_gems_after=before_gem_ids,
            )
        if before_gem_ids:
            frame, image, inventory = self._deposit_all_item(
                frame=frame,
                image=image,
                inventory=inventory,
                item_id="uncut_ruby",
                expected_ore_before=0,
                expected_gems_before=before_gem_ids,
                expected_ore_after=0,
                expected_gems_after=[],
            )
        if not self._known_composition(
            inventory,
            ore_count=0,
            gem_ids=[],
            empty_count=28,
        ):
            raise BankUnproven("deposit_did_not_prove_empty_inventory")
        self.record(
            "DEPOSIT_VERIFIED",
            before_occupied_count=28,
            before_ore_count=before_ore_count,
            before_gem_count=len(before_gem_ids),
            before_gem_item_ids=before_gem_ids,
            after_occupied_count=0,
            after_ore_count=0,
            after_gem_count=0,
            empty_slots=28,
            frame_id=frame.frame_id,
        )
        controls = self.vision.bank_controls(image)
        if controls is None:
            raise BankUnproven("bank_X_not_verified")
        self.click(frame, controls[1].centre, "CLOSE_BANK_X")
        closed_proofs = 0
        for _ in range(15):
            frame, image = self.observe("verify-bank-closed")
            tabs = self.vision.match(
                image,
                "normal_tabs",
                (450, image.shape[0] - 350, image.shape[1], image.shape[0] - 230),
            )
            after = self.vision.inventory(image)
            closed = not self.vision.bank_controls(image) and tabs.score > 0.82
            closed_proofs = closed_proofs + 1 if closed and after["empty_count"] == 28 else 0
            if closed_proofs >= 2:
                self.record("BANK_CLOSED_VERIFIED", empty_slots=28, frame_id=frame.frame_id)
                return {
                    "status": "PASS",
                    "success": True,
                    "deposit_verified": True,
                    "bank_closed_verified": True,
                    "before_occupied_count": 28,
                    "before_ore_count": before_ore_count,
                    "before_gem_count": len(before_gem_ids),
                    "before_gem_item_ids": before_gem_ids,
                    "deposited_ore_count": before_ore_count,
                    "deposited_gem_count": len(before_gem_ids),
                    "deposited_gem_item_ids": before_gem_ids,
                    "after_occupied_count": 0,
                    "after_ore_count": 0,
                    "after_gem_count": 0,
                    "manual_operator_clicks": False,
                    "last_image": frame.evidence_path,
                }
            self.backend.wait(0.25)
        raise BankUnproven("bank_close_not_verified")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--open-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]

    def git(*a: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *a], check=True, capture_output=True, text=True
        ).stdout.strip()

    if not args.live or args.confirm != "DEPOSIT_IRON_AND_CLOSE_BANK":
        parser.error("Explicit live deposit-and-close confirmation required")
    if git("rev-parse", "HEAD") != args.sha or git("status", "--porcelain", "--untracked-files=no"):
        parser.error("Exact clean committed build required")
    for rel in (
        "src/mining_automation/bank_runtime.py",
        "src/mining_automation/bank_vision.py",
        "src/mining_automation/bank_profiles/iron_bank.npz",
        "src/mining_automation/bank_profiles/iron_bank.json",
        "src/mining_automation/perception/inventory/retained_iron.py",
    ):
        git("ls-files", "--error-unmatch", rel)
    backend = None
    runner = None
    try:
        backend = NativeRouteBackend(
            args.hwnd,
            args.output,
            expected_title=args.title,
            focus_existing=True,
            stop_file=args.output / "STOP",
        )
        runner = BankRunner(backend)
        result = runner.run(args.open_only)
    except (Exception, KeyboardInterrupt) as error:
        result = {"status": "STOP", "success": False, "reason": f"{type(error).__name__}:{error}"}
    result["git_sha"] = args.sha
    result["evidence_origin"] = "standalone_bank_program"
    result["events"] = [] if runner is None else runner.events
    result["click_count"] = 0 if backend is None else backend.delivered_click_count
    result["start_window"] = None if backend is None else backend.initial
    try:
        result["end_window"] = None if backend is None else backend.snapshot()
    except Exception:
        result["end_window"] = None
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ("events", "start_window", "end_window")},
            indent=2,
        ),
        flush=True,
    )
    return 0 if result["status"] in ("PASS", "OPEN_STAGE_PASS") else 2


if __name__ == "__main__":
    raise SystemExit(main())
