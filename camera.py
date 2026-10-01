"""Camera capture for the cauldron's item tray.

Run directly to grab a single still:

    .venv/bin/python camera.py               # Write to generated/captured_item.jpg.
    .venv/bin/python camera.py test.jpg      # Write to the given path.
    .venv/bin/python camera.py --warmup 2    # Give auto-exposure longer to settle.
    .venv/bin/python camera.py --sweep-focus # Recalibrate CAMERA_FOCUS below.
"""

from __future__ import annotations

import argparse
import statistics
import time
from pathlib import Path

import cv2

CAMERA_INDEX = 0
# Auto-exposure only adapts while frames are being pulled off the camera, so
# discard a few before the real capture. Raise it with --warmup if a darker
# scene needs longer to settle.
CAMERA_WARMUP_SEC = 0.5
# The camera's continuous autofocus does not reliably lock in the tray's dim,
# close-range, low-contrast scene: left alone, it settles wherever, and one
# capture measured 8-10x softer than a fixed focus a few steps away. Sharpness
# swept sharply peaked and stable at this value with the camera at its current
# distance from the tray; re-run --sweep-focus and update this if that
# distance ever changes.
CAMERA_FOCUS = 9
GENERATED_DIR = Path("generated")
DEFAULT_OUTPUT = GENERATED_DIR / "captured_item.jpg"


def capture_frame(
    output_path: str | Path = DEFAULT_OUTPUT,
    *,
    warmup_sec: float = CAMERA_WARMUP_SEC,
    focus: int = CAMERA_FOCUS,
) -> Path | None:
    """Capture one frame from the USB camera and write it to ``output_path``.

    Turns backlight compensation off and pins the focus (see CAMERA_FOCUS),
    then discards frames for ``warmup_sec`` so auto-exposure and white balance
    settle on the lit scene. Returns the path on success, or ``None`` if the
    camera could not be opened or the grab failed.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        cap.release()
        print("Error: could not open camera.")
        return None

    # Backlight compensation drives the exposure up to rescue a subject lit
    # from behind, which blows the tray out instead: 61% of pixels clipped to
    # white with it on versus 3% off. The camera stores both this and the
    # autofocus lens position in its own hardware, so force known-good values
    # rather than trusting how the device was last left.
    cap.set(cv2.CAP_PROP_BACKLIGHT, 0)
    cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
    cap.set(cv2.CAP_PROP_FOCUS, focus)

    # read() blocks until the next frame, so this paces itself at the frame rate.
    deadline = time.monotonic() + warmup_sec
    while time.monotonic() < deadline:
        cap.read()

    ret, frame = cap.read()
    cap.release()

    if not ret:
        print("Error: camera frame capture failed.")
        return None

    cv2.imwrite(str(output_path), frame)
    return output_path


def _sharpness(frame: cv2.typing.MatLike) -> float:
    """Laplacian variance: higher means more in-focus detail."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(gray, cv2.CV_64F).var()


def sweep_focus(
    *, warmup_sec: float = CAMERA_WARMUP_SEC, samples: int = 4
) -> None:
    """Measure sharpness across the focus range and report the sharpest value.

    Point the camera at the tray with something textured on it first (plain
    or shiny surfaces do not give the Laplacian much to measure). Update
    CAMERA_FOCUS with whatever this settles on.
    """
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("Error: could not open camera.")
        return
    cap.set(cv2.CAP_PROP_BACKLIGHT, 0)
    cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)

    best: tuple[int, float] | None = None
    for focus in range(0, 41):
        cap.set(cv2.CAP_PROP_FOCUS, focus)
        deadline = time.monotonic() + warmup_sec
        while time.monotonic() < deadline:
            cap.read()
        readings = []
        for _ in range(samples):
            ret, frame = cap.read()
            if ret:
                readings.append(_sharpness(frame))
        if not readings:
            continue
        median = statistics.median(readings)
        print(f"focus={focus:2d}  sharpness={median:8.1f}")
        if best is None or median > best[1]:
            best = (focus, median)

    cap.release()
    if best is not None:
        print(f"\nSharpest: focus={best[0]} (sharpness={best[1]:.1f})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture a single still from the cauldron's USB camera."
    )
    parser.add_argument(
        "output",
        nargs="?",
        default=str(DEFAULT_OUTPUT),
        help=f"Where to write the JPEG (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--warmup",
        type=float,
        default=CAMERA_WARMUP_SEC,
        metavar="SECONDS",
        help="Seconds to discard frames for so auto-exposure settles "
        f"(default: {CAMERA_WARMUP_SEC:g}).",
    )
    parser.add_argument(
        "--focus",
        type=int,
        default=CAMERA_FOCUS,
        metavar="0-40",
        help=f"Fixed lens focus position (default: {CAMERA_FOCUS}).",
    )
    parser.add_argument(
        "--sweep-focus",
        action="store_true",
        help="Measure sharpness across the focus range instead of capturing, "
        "to find the value for CAMERA_FOCUS.",
    )
    args = parser.parse_args()

    if args.sweep_focus:
        sweep_focus(warmup_sec=args.warmup)
        return

    path = capture_frame(args.output, warmup_sec=args.warmup, focus=args.focus)
    if path is None:
        raise SystemExit(1)
    print(f"Saved {path}")


if __name__ == "__main__":
    main()
