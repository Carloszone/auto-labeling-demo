"""Exact frame-index access for constant-frame-rate pipeline videos."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import cv2
import numpy as np


def iter_video_frames(path: Path | str, expected_count: int) -> Iterator[tuple[int, np.ndarray | None]]:
    """Decode a CFR video sequentially and preserve its zero-based frame indexes."""

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"cannot open video: {path}")
    try:
        for index in range(expected_count):
            ok, frame = capture.read()
            yield index, frame if ok else None
    finally:
        capture.release()


def read_video_frames(path: Path | str, indexes: list[int]) -> dict[int, np.ndarray]:
    """Read requested CFR frames by index, validating every decoder position."""

    requested = sorted(set(int(index) for index in indexes))
    if not requested:
        return {}
    if requested[0] < 0:
        raise ValueError("video frame indexes must not be negative")
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"cannot open video: {path}")
    output: dict[int, np.ndarray] = {}
    try:
        first, last = requested[0], requested[-1]
        if not capture.set(cv2.CAP_PROP_POS_FRAMES, first):
            raise ValueError(f"cannot seek video frame: path={path}, index={first}")
        requested_set = set(requested)
        for index in range(first, last + 1):
            ok, frame = capture.read()
            decoded_next = int(round(capture.get(cv2.CAP_PROP_POS_FRAMES)))
            if not ok or frame is None or decoded_next != index + 1:
                raise ValueError(
                    f"cannot decode exact video frame: path={path}, index={index}, "
                    f"decoder_next_index={decoded_next}"
                )
            if index in requested_set:
                output[index] = frame
    finally:
        capture.release()
    return output
