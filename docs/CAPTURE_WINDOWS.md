# Windows RuneLite Capture Backend

Milestone: **M2 — Real platform capture**
Package: `src/mining_automation/capture/windows/`

The first real `CaptureBackend` implementation from Issue #1's protocol,
targeting a RuneLite window on Windows. See `docs/CAPTURE.md` for the
platform-independent capture layer this backend plugs into; this document
covers only what's specific to Windows.

## Scope

In scope: finding and capturing a RuneLite window's client area, surviving
move/resize, handling DPI scaling, reporting typed failures. Out of scope,
per Issue #5: rock/ore detection, inventory recognition, navigation, banking,
mouse/keyboard interaction, GUI, recovery policy beyond emitting the correct
typed errors, and authentication.

## Public API

```python
from mining_automation.capture import CaptureSource
from mining_automation.capture.windows import WindowsCaptureBackend

with CaptureSource(WindowsCaptureBackend()) as source:
    frame = source.capture()
```

| Symbol | Role |
|---|---|
| `WindowsCaptureBackend` | The `CaptureBackend` implementation. |
| `Win32Api` | Protocol seam between the backend and the OS. |
| `RealWin32Api` | Production implementation, built on `ctypes`. |
| `WindowInfo` | Plain-data description of a candidate window. |
| `select_window` | Pure, deterministic window selection. |
| `DEFAULT_TITLE_SUBSTRING` | `"runelite"`. |

Test doubles: `capture.windows.testing.FakeWin32Api`.

## Dependency rationale

**No new dependency was added.** Everything is built on `ctypes`, part of the
standard library.

Two named alternatives were evaluated and rejected:

- **`pywin32`** cannot be installed on this project's Linux CI at all — it
  ships no Linux wheels. Even scoped with a `sys_platform == "win32"`
  environment marker so `pip install` skips it safely, mypy would still need a
  separate stub package (`types-pywin32`) to resolve its types on a
  non-Windows runner, adding a second dependency to solve a problem `ctypes`
  doesn't have.
- **`mss`** only does bounding-box screen grabs — it has no window
  enumeration, geometry, or minimized-state API. Window discovery still has to
  be hand-written against `ctypes` regardless, so adding `mss` would not have
  reduced the amount of platform code, only added a dependency for a single
  step (the final pixel grab) that `ctypes` already does directly. Notably,
  `mss`'s own Windows backend is itself built on raw `ctypes` calls, not
  `pywin32`.

`ctypes` was confirmed to typecheck cleanly under `mypy --strict` on this
project's Linux CI runner before being adopted — see "Cross-platform
typechecking" below. `dependencies` in `pyproject.toml` is unchanged.

## Cross-platform typechecking

CI runs `mypy src` on Ubuntu. typeshed defines `ctypes.WinDLL` and
`ctypes.WINFUNCTYPE` only under `sys.platform == "win32"`, so mypy running
with its default platform cannot resolve them — confirmed directly:

```python
ctypes.WinDLL("user32")  # mypy (default/Linux): error: Module has no attribute "WinDLL"
```

Every other `ctypes`/`ctypes.wintypes` symbol used here (`RECT`, `POINT`,
`HWND`, `byref`, structure definitions, `.restype`/`.argtypes`) typechecks
identically on any platform. So the two DLL-loading calls are isolated to two
one-line helpers in `_win32_calls.py`, each carrying one explicit, justified
`# type: ignore[attr-defined]`, and nothing else in the package needs any
suppression. This was verified against `mypy --strict` before being adopted as
the pattern, not assumed.

## Import safety on non-Windows platforms

`_win32_calls.py` loads `user32.dll` and `gdi32.dll` at module import time.
Importing this module on a non-Windows platform raises immediately
(`AttributeError: module 'ctypes' has no attribute 'WinDLL'`), so nothing may
import it except `RealWin32Api.__init__`, and only *after* that constructor
has already confirmed `sys.platform == "win32"`:

```python
class RealWin32Api:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError(...)
        from . import _win32_calls  # only reached on confirmed Windows
```

Everything else — `WindowsCaptureBackend`, `Win32Api`, `WindowInfo`,
`window_selector`, `dpi`, `geometry`, `bmp` — is plain Python with no
platform-gated import, so it is fully constructible, protocol-checkable, and
unit-testable on any platform, including this project's Linux CI. This is
verified directly:

```python
from mining_automation.capture import CaptureBackend
from mining_automation.capture.windows import WindowsCaptureBackend
from mining_automation.capture.windows.testing import FakeWin32Api

backend = WindowsCaptureBackend(win32_api=FakeWin32Api())
isinstance(backend, CaptureBackend)  # True, on Linux
```

The raw DLL layer still cannot execute on Linux, but its GDI ownership rules
are isolated in `gdi_resources.py`, which is platform-neutral and covered by
deterministic failure-path tests. A separate Windows GitHub Actions smoke job
imports `RealWin32Api`, loads the real DLL bindings, requests DPI awareness,
and enumerates windows. That smoke job catches Windows-only import/signature
failures; it still cannot prove RuneLite pixel correctness without RuneLite.

## Window selection

Selection (`window_selector.py`) is pure — plain data in, plain data out, no
OS call — which is what makes it exhaustively unit-testable.

**Matching.** A window is a candidate if it is visible and its title contains
`title_substring` case-insensitively (default `"runelite"`). A substring
match, not exact, because RuneLite's title varies by launch mode and
logged-in state (`"RuneLite"` vs. `"RuneLite - PlayerName"`, or a custom name
via launch arguments or plugins). Not a fixed window and not a coordinate, per
the issue's requirement.

**Tie-break, when more than one candidate matches**, in order:

1. not minimized before minimized
2. larger client area before smaller
3. lower `hwnd` before higher (arbitrary, but stable)

Fully deterministic — the same desktop state always selects the same window.
A minimized candidate is not discarded: a session starting with RuneLite
minimized should still resolve to it, so the backend reports a clear
"minimized" failure on the first capture rather than a misleading "no window
found."

## Capture strategy

1. Confirm the window still exists and is not minimized.
2. Read the full window bounds (`GetWindowRect`) and the client rect
   (`GetClientRect`).
3. Capture the **full window** via `PrintWindow` with
   `PW_RENDERFULLCONTENT`, into an in-memory bitmap.
4. Crop to the client area in memory via `BitBlt`, using an offset computed
   from `ClientToScreen` (this offset arithmetic is the one piece of Win32
   geometry math that's pure enough to unit test directly, and is tested).
5. Restore the original bitmap in the crop device context before calling
   `GetDIBits` (the API requires the target bitmap to be deselected), request
   top-down row order (`biHeight` negative), and require exactly the requested
   number of scanlines.
6. Release every GDI handle through the tested `GdiBitmapSurface` lifecycle.
   Partial allocation and selection failures clean up every resource already
   created; cleanup failures surface on a normal path and never replace an
   exception already in flight.

**Why crop after capturing the full window**, rather than capturing the
client area directly with `PW_CLIENTONLY`: combining `PW_CLIENTONLY` with
`PW_RENDERFULLCONTENT` is documented across Windows versions as unreliable.
Capturing the full window first makes the client-area crop independent of
window decoration and desktop position. It does **not** guarantee that
`PrintWindow` can rasterize every Java2D/OpenGL/hardware-accelerated RuneLite
mode; that remains an explicit real-machine acceptance test.

**Pixel format.** `PixelFormat.BGRA8888` — Windows' native 32bpp GDI byte
order, and already the default in `capture.frame` from Issue #1.

## Window handle caching

The resolved window is cached (as a `WindowInfo`, not just an `hwnd`, so
`selected_window` can report identity) across successful captures rather than
re-resolved every frame — the same handle stays valid across move and resize,
and geometry is re-read fresh on every single capture regardless. **Any**
capture failure clears the cache, so the next attempt re-runs full discovery
rather than retrying a handle that may no longer point at anything. This
means a sustained failure (RuneLite minimized for a while) re-enumerates
windows on every attempt rather than caching the failure — simpler and more
robust than trying to distinguish "same window, temporarily unavailable" from
"window gone, need a different one" from the exception alone, at the cost of
that extra enumeration during failures.

`close()` clears the cache too, so a `open() → close() → open()` cycle (a
session pause, in the wider application) never trusts a handle cached before
the pause — the next capture re-resolves from scratch.

## DPI and scaling

`declare_dpi_awareness()` is called once, from `WindowsCaptureBackend.open()`,
and requests per-monitor DPI awareness (`DPI_AWARENESS_CONTEXT_PER_MONITOR_
AWARE_V2`, Windows 10 1703+), falling back through the Windows 8.1 `shcore`
API and the Vista+ legacy API, stopping at the first that succeeds. This call
is documented as best-effort and never raises — a failure commonly just means
awareness was already declared elsewhere, such as by an application manifest,
which is not an error condition.

With awareness declared, every subsequent `GetClientRect`/`GetWindowRect`
call already returns **physical pixels directly** — this is the primary DPI
defense, and it means capture dimensions need no further scaling math.

What remains, and is genuinely tested, is `dpi.scale_factor_for_dpi()`: a
pure conversion (`dpi / 96`) used for diagnostics and exposed on the backend
as `current_dpi`, reported by the validation harness.

## Local validation harness

`tools/windows_capture_check.py` — development-only, not imported by the
production application, per `tools/README.md`. Built as a thin script over the
real `CaptureSource` + `WindowsCaptureBackend`, not a reimplementation of
capture logic.

```bash
python tools/windows_capture_check.py
python tools/windows_capture_check.py --title "RuneLite" --frames 20 --interval 0.5
python tools/windows_capture_check.py --save-frame diagnostics/last.bmp
```

Reports, per the issue's minimum list: selected window identity and title,
capture dimensions, pixel format, frame id/timestamp progression through the
real `CaptureSource`, and a clear typed message on every failure (minimized,
unavailable, or threshold-exceeded). `--save-frame` is opt-in and off by
default; when given, it writes only the RuneLite client's own last-captured
content area as a BMP — never the desktop or any other window — using a
small stdlib-only encoder (`capture/windows/bmp.py`, no image dependency
added) since the captured payload is already byte-for-byte what a 32bpp
top-down BMP wants. No credentials are read, stored, or logged anywhere in
this tool.

Running the harness on a non-Windows machine fails immediately with one clear
line (`Cannot run this tool here: RealWin32Api requires Windows...`) rather
than a traceback, since `WindowsCaptureBackend()`'s default construction
already fails fast for exactly this case.

The harness's own reporting logic was exercised with a scripted `FakeWin32Api`
standing in for `RealWin32Api` before this PR was opened — confirming that
report ordering, the save-frame path, and the failure-reporting path are all
correct. That exercise is not a substitute for the real-machine gate below;
it catches bugs in the harness's own logic, not in the Win32 calls it drives
in production, none of which run under a fake.

## Windows CI smoke

`.github/workflows/windows-capture-smoke.yml` runs on `windows-latest` when the
Windows capture implementation changes. It imports the real ctypes layer,
constructs `RealWin32Api`, requests DPI awareness, and exercises window
enumeration. Platform-neutral GDI lifecycle tests also run there. This catches
DLL/signature/import regressions, but the hosted runner has no RuneLite client,
so it is not the real-machine acceptance gate.

## Real-machine validation — pending

**Not performed.** This environment has no display, no Windows, and no
RuneLite client — everything in this PR was built and verified without one,
exactly as Issue #5 anticipated as a possible outcome. Every module that
*can* be exercised without Windows carries dedicated automated tests. What
cannot be exercised this way is the actual behavior of `_win32_calls.py`
against a real window: whether `PrintWindow(..., PW_RENDERFULLCONTENT)`
correctly rasterizes RuneLite's specific rendering mode (Java2D software vs.
OpenGL-accelerated), whether the client-area crop offset is exactly right
against real window decoration, and whether DPI awareness behaves as
documented across real multi-monitor / mixed-DPI setups.

To close this gate, on a real Windows machine with RuneLite open:

```bash
python -m pip install -e .
python tools/windows_capture_check.py --frames 20 --interval 0.5 --save-frame diagnostics/check.bmp
```

Confirm: repeated successful captures with strictly increasing `frame_id` and
`t`; correct, stable `width`/`height` matching the RuneLite window's actual
client area; the saved BMP visually shows RuneLite's content, not a black or
corrupted frame; minimizing RuneLite produces a clear "minimized" failure
line, and restoring it recovers on the next capture; moving or resizing the
window between captures is reflected without restarting the tool; and, if a
non-100% display scale is available to test, `current_dpi` reports correctly
and captured dimensions remain physically correct.

## Known unsupported configurations

- **Non-Windows platforms.** By design — `RealWin32Api` refuses construction
  outside `sys.platform == "win32"`.
- **Windows versions predating the legacy DPI fallback** (older than Vista)
  are not supported; RuneLite itself does not target them.
- **Multiple simultaneous RuneLite instances** are handled by the documented
  deterministic tie-break, not by capturing more than one — this backend
  targets exactly one window per `WindowsCaptureBackend` instance.
- **Rendering-mode correctness for hardware-accelerated content** is a
  documented risk area (see above), not yet confirmed on a real machine.
- **Multi-monitor mixed-DPI setups** — the awareness/scaling approach is
  standard and documented, but has not been validated against a real
  multi-monitor configuration.
- **Layered or owner-drawn overlay windows** on top of RuneLite (e.g. certain
  overlay software) are not accounted for; `PrintWindow` captures RuneLite's
  own content only.

## Tests

`tests/test_windows_capture.py` covers the backend, selection, geometry,
DPI, error mapping, and BMP behavior. `tests/test_windows_gdi_resources.py`
covers transactional GDI allocation, SelectObject failures, deselection before
pixel reads, partial scanline rejection, cleanup ordering, and exception
preservation. `tests/test_windows_capture_real.py` is a Windows-only smoke test
for the real DLL layer.

Groups: protocol conformance · window selection (matching, visibility
filtering, multi-candidate tie-break, all-minimized, degenerate-size
exclusion) · DPI scale-factor arithmetic · client-area offset arithmetic ·
successful discovery and capture, including full integration through the real
`CaptureSource` · no matching window · minimized/unavailable, including cache
invalidation and recovery once the window becomes available again · resize
and move between captures · invalid dimensions/payload propagating correctly
through the unmodified Issue #1 `Frame.from_raw` validation · open/close
idempotency and cache-clearing on close · exception mapping into the shared
capture taxonomy, including the existing failure-threshold policy exercised
against a real backend for the first time · `RealWin32Api`'s own delegation
logic, using a stubbed `_win32_calls` module injected via `sys.modules` since
the real one cannot import on this platform · the BMP encoder.
