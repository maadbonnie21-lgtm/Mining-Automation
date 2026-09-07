"""Live iron-only deposit followed by an explicitly verified bank-X click."""

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

    def run(self, open_only: bool = False) -> dict[str, Any]:
        self.hover((510, 300))
        frame, image = self.observe("banking-start")
        inventory = self.vision.inventory(image)
        self.record("INVENTORY_BEFORE", **inventory)
        if inventory["ore_count"] != 28 or inventory["unknown_count"]:
            raise BankUnproven("requires_verified_28_iron_ore")
        if not self.vision.bank_controls(image):
            booth = self.vision.match(image, "booth", (0, 100, 530, image.shape[0] - 150))
            if booth.score < 0.94:
                raise BankUnproven("bank_booth_unproven:" + str(booth.score))
            self.hover(booth.centre)
            frame, image = self.observe("bank-booth-hover")
            proof = max(
                (
                    self.vision.match(image, key, (0, 24, 330, 60), text=True)
                    for key in ("bank_hover", "bank_hover_original", "bank_hover_live2")
                ),
                key=lambda match: match.score,
            )
            # Text sampling changes across captures. It is diagnostic for OPEN,
            # not the input authority: the precise gold booth appearance is.
            # No item action is possible until the bank title and X both verify.
            self.record(
                "BOOTH_TARGET_VERIFIED", appearance_score=booth.score, hover_text_score=proof.score
            )
            self.click(frame, booth.centre, "OPEN_BANK_BOOTH")
            frame, image = self.wait_open()
        if open_only:
            return {"status": "OPEN_STAGE_PASS", "success": False, "deposit_verified": False}
        inventory = self.vision.inventory(image)
        if inventory["ore_count"] != 28 or inventory["unknown_count"]:
            raise BankUnproven("bank_inventory_is_not_28_iron_ore")
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
        slot = inventory["ore_slots"][0]
        ore_point = ((slot[0] + slot[2]) // 2, (slot[1] + slot[3]) // 2)
        self.hover(ore_point)
        frame, image = self.observe("iron-deposit-all-hover")
        if not self.vision.bank_controls(image):
            raise BankUnproven("bank_closed_before_deposit")
        proof = self.vision.match(image, "deposit_text", (0, 24, 350, 65), text=True)
        self.record("DEPOSIT_HOVER", score=proof.score)
        # Actual item action is proven by bank OPEN + selected Quantity-All
        # + the identified player-inventory iron slot. Text is diagnostic.
        selected = self.vision.match(image, "quantity_all_selected", all_box)
        self.record("QUANTITY_ALL_VERIFIED", score=selected.score)
        if selected.score < 0.90:
            raise BankUnproven("quantity_All_not_selected")
        inventory = self.vision.inventory(image)
        if inventory["ore_count"] != 28 or inventory["unknown_count"]:
            raise BankUnproven("inventory_changed_before_deposit")
        self.click(frame, ore_point, "DEPOSIT_ALL_IRON_ORE_ONLY")
        self.hover((510, 300))
        empty_proofs = 0
        for _ in range(15):
            frame, image = self.observe("verify-deposit-empty")
            if not self.vision.bank_controls(image):
                raise BankUnproven("bank_not_open_during_deposit_verification")
            after = self.vision.inventory(image)
            empty_proofs = empty_proofs + 1 if after["empty_count"] == 28 else 0
            if empty_proofs >= 2:
                self.record(
                    "DEPOSIT_VERIFIED",
                    before_ore_count=28,
                    after_ore_count=0,
                    empty_slots=28,
                    frame_id=frame.frame_id,
                )
                break
            self.backend.wait(0.25)
        else:
            raise BankUnproven("deposit_did_not_prove_empty_inventory")
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
                    "before_ore_count": 28,
                    "after_ore_count": 0,
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
