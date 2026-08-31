from __future__ import annotations

from pathlib import Path

import pytest

from mining_automation.capture import Frame, PixelFormat, RawFrame
from mining_automation.perception.resource import ResourceVisualState
from mining_automation.perception.resource_fixtures import (
    ResourceFixtureAnnotation,
    add_resource_annotation,
    build_replay_manifest,
    load_resource_fixture_draft,
    mark_resource_fixture_reviewed,
    save_resource_fixture_draft,
    write_resource_fixture_draft,
)


def make_bgra_frame(
    pixels: list[tuple[int, int, int]], width: int, height: int, *, frame_id: int = 1
) -> Frame:
    payload = bytearray()
    for red, green, blue in pixels:
        payload.extend((blue, green, red, 255))
    return Frame.from_raw(
        RawFrame(bytes(payload), width, height, PixelFormat.BGRA8888),
        frame_id=frame_id,
        captured_monotonic_s=float(frame_id),
    )


def test_fixture_draft_round_trip_and_replay_manifest(tmp_path: Path) -> None:
    frame = make_bgra_frame([(20, 80, 20), (20, 80, 20), (120, 40, 30), (120, 40, 30)] * 2, 4, 2)
    paths = write_resource_fixture_draft(
        frame,
        tmp_path,
        dataset_id="varrock-east-v1",
        case_id="available-001",
        location_id="varrock-east-mine",
        tags=("real",),
        provenance={"source": "unit-test"},
    )

    assert paths.frame.read_bytes() == frame.payload
    assert paths.preview.read_bytes().startswith(b"BM")
    draft = load_resource_fixture_draft(paths.draft)
    annotation = ResourceFixtureAnnotation(
        resource_id="iron-1",
        ore_label="iron",
        state=ResourceVisualState.AVAILABLE,
        region=(2, 0, 2, 2),
        confidence_min=0.8,
        confidence_max=1.0,
    )
    draft = add_resource_annotation(draft, annotation)
    draft = mark_resource_fixture_reviewed(draft)
    save_resource_fixture_draft(draft, paths.draft)

    loaded = load_resource_fixture_draft(paths.draft)
    assert loaded == draft
    manifest_path = tmp_path / "manifest.json"
    manifest = build_replay_manifest([loaded], manifest_path)
    case = manifest["cases"][0]
    assert case["expected_observations"][0]["kind"] == "resource.available"
    assert case["expected_observations"][0]["label"] == "iron"


def test_fixture_capture_never_overwrites(tmp_path: Path) -> None:
    frame = make_bgra_frame([(0, 0, 0)] * 8, 4, 2)
    kwargs = dict(
        dataset_id="dataset",
        case_id="case-1",
        location_id="mine",
    )
    write_resource_fixture_draft(frame, tmp_path, **kwargs)
    with pytest.raises(FileExistsError):
        write_resource_fixture_draft(frame, tmp_path, **kwargs)


@pytest.mark.parametrize("winning_path", ["frame", "preview", "draft"])
def test_concurrent_fixture_writer_winner_is_preserved_exactly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    winning_path: str,
) -> None:
    frame = make_bgra_frame([(0, 0, 0)] * 8, 4, 2)
    paths = {
        "frame": tmp_path / "frames" / "case-1.raw",
        "preview": tmp_path / "previews" / "case-1.bmp",
        "draft": tmp_path / "drafts" / "case-1.json",
    }
    target = paths[winning_path]
    winner_payload = f"concurrent-{winning_path}-winner".encode()
    real_open = Path.open

    def open_after_concurrent_win(
        path: Path,
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ):
        if path == target and mode == "xb":
            with real_open(path, "wb") as winner:
                winner.write(winner_payload)
            raise FileExistsError(f"simulated concurrent winner: {path}")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_after_concurrent_win)

    with pytest.raises(FileExistsError, match="simulated concurrent winner"):
        write_resource_fixture_draft(
            frame,
            tmp_path,
            dataset_id="dataset",
            case_id="case-1",
            location_id="mine",
        )

    assert target.read_bytes() == winner_payload
    assert all(not path.exists() for name, path in paths.items() if name != winning_path)


def test_unreviewed_fixture_cannot_enter_replay_manifest(tmp_path: Path) -> None:
    frame = make_bgra_frame([(0, 0, 0)] * 8, 4, 2)
    paths = write_resource_fixture_draft(
        frame,
        tmp_path,
        dataset_id="dataset",
        case_id="case-1",
        location_id="mine",
    )
    draft = load_resource_fixture_draft(paths.draft)
    with pytest.raises(ValueError, match="not been reviewed"):
        build_replay_manifest([draft], tmp_path / "manifest.json")


def test_unsafe_case_id_is_rejected(tmp_path: Path) -> None:
    frame = make_bgra_frame([(0, 0, 0)] * 8, 4, 2)
    with pytest.raises(ValueError, match="case_id"):
        write_resource_fixture_draft(
            frame,
            tmp_path,
            dataset_id="dataset",
            case_id="../escape",
            location_id="mine",
        )
