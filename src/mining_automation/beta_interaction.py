"""Bounded cursor paths and inward point selection; no game perception or OS imports."""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable
from typing import Any


class InputExpired(RuntimeError):
    pass


def require_fresh(deadline: float, now: float) -> None:
    if not math.isfinite(deadline) or not math.isfinite(now) or now >= deadline:
        raise InputExpired("source_expired_before_down; zero_click; fresh_reacquisition_required")


def sample_interior(
    region: tuple[int, int, int, int],
    *,
    previous: tuple[int, int] | None = None,
    margin: int = 2,
    rng: Any = None,
) -> tuple[int, int]:
    if len(region) != 4 or any(type(value) is not int for value in region):
        raise ValueError("Interaction region must contain four integer coordinates")
    x, y, w, h = region
    if (
        type(margin) is not int
        or margin < 1
        or x < 0
        or y < 0
        or w <= margin * 2
        or h <= margin * 2
    ):
        raise ValueError("No positive inward interaction area")
    width, height = w - margin * 2, h - margin * 2
    count = width * height
    old = None
    if previous is not None:
        ox, oy = previous[0] - x - margin, previous[1] - y - margin
        if 0 <= ox < width and 0 <= oy < height:
            old = oy * width + ox
    generator = rng or random.SystemRandom()
    index = generator.randrange(count - 1 if old is not None and count > 1 else count)
    if old is not None and count > 1 and index >= old:
        index += 1
    return x + margin + index % width, y + margin + index // width


def require_interior(
    point: tuple[int, int], region: tuple[int, int, int, int], margin: int = 2
) -> None:
    x, y, w, h = region
    if (
        any(type(value) is not int for value in (*point, *region))
        or not x + margin <= point[0] < x + w - margin
        or not y + margin <= point[1] < y + h - margin
    ):
        raise ValueError("Selected point is outside the current verified inward region")


def smooth_move(
    api: Any,
    target: tuple[int, int],
    *,
    check: Callable[[], None],
    duration_s: float = 0.18,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    trace: Callable[[tuple[int, int]], None] | None = None,
) -> None:
    if not math.isfinite(duration_s) or not 0 < duration_s <= 0.5:
        raise ValueError("Cursor travel must have a finite bounded duration")
    if any(type(value) is not int for value in target):
        raise ValueError("Cursor target must be an integer point")
    check()
    start = previous = api.cursor_position()
    if start == target:
        if deadline is not None:
            require_fresh(deadline, clock())
        return
    steps = max(2, math.ceil(math.dist(start, target) / 8), math.ceil(duration_s / 0.01))
    started = clock()
    for index in range(1, steps + 1):
        check()
        if deadline is not None:
            require_fresh(deadline, clock())
        if api.cursor_position() != previous:
            raise RuntimeError("human_cursor_displacement; no_fight_for_pointer")
        if api.left_button_is_down() or api.middle_button_is_down() or api.key_is_down(0x02):
            raise RuntimeError("human_mouse_button_held")
        t = index / steps
        fraction = 10 * t**3 - 15 * t**4 + 6 * t**5
        point = (
            round(start[0] + (target[0] - start[0]) * fraction),
            round(start[1] + (target[1] - start[1]) * fraction),
        )
        if not api.move_cursor(*point) or api.cursor_position() != point:
            raise RuntimeError("cursor_path_delivery_failed")
        previous = point
        if trace is not None:
            trace(point)
        wait = started + duration_s * t - clock()
        if wait > 0:
            sleep(wait)
    check()
    if deadline is not None:
        require_fresh(deadline, clock())
    if api.cursor_position() != target:
        raise RuntimeError("cursor_endpoint_displaced")


def click_at_proven_point(
    api: Any,
    screen_point: tuple[int, int],
    *,
    hwnd: int,
    check: Callable[[], None],
    wait: Callable[[float], None],
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
    receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Never moves/recenters. Release only this action's button in every exception path."""
    result = receipt if receipt is not None else {}
    check()
    require_fresh(deadline, clock())
    if api.cursor_position() != screen_point or api.root_window_at_point(*screen_point) != hwnd:
        raise RuntimeError("hover_dispatch_point_mismatch; zero_click")
    if api.left_button_is_down() or api.middle_button_is_down() or api.key_is_down(0x02):
        raise RuntimeError("human_mouse_button_held; zero_click")
    check()
    require_fresh(deadline, clock())
    actual = api.cursor_position()
    if actual != screen_point or api.root_window_at_point(*actual) != hwnd:
        raise RuntimeError("point_changed_at_final_dispatch_gate; zero_click")
    result.update(screen_point=list(actual), down=None, up=0, released=False, down_attempted=False)
    check()
    require_fresh(deadline, clock())
    attempted = False
    try:
        attempted = True
        result["down_attempted"] = True
        result["down"] = api.send_mouse_button(button_up=False)
        result["down_monotonic_s"] = clock()
        result["after_down_screen_point"] = list(api.cursor_position())
        if tuple(result["after_down_screen_point"]) != screen_point:
            raise RuntimeError("cursor_changed_at_mouse_down; delivered_input_requires_review")
        if result["down"] != 1:
            raise RuntimeError("mouse_down_not_delivered")
        wait(0.05)
    finally:
        if attempted:
            for _ in range(3):
                result["up"] = api.send_mouse_button(button_up=True)
                if result["up"] == 1 and not api.left_button_is_down():
                    result["released"] = True
                    break
    if not result["released"]:
        raise RuntimeError("owned_mouse_release_unconfirmed")
    check()
    if api.cursor_position() != screen_point:
        raise RuntimeError("cursor_changed_during_owned_click")
    return result
