"""Official decoder adapters. No QR decoding algorithm is implemented here."""
from __future__ import annotations

from dataclasses import dataclass

from .protocol import Limits, ProtocolError, parse_frame
from .state import ReceiverState


class VideoError(RuntimeError):
    pass


@dataclass
class Stats:
    frames: int = 0
    unique_chunks: int = 0
    duplicates: int = 0
    invalid_frames: int = 0
    decode_errors: int = 0
    last_invalid_frame_error: str | None = None


def accept_payload(payload, state: ReceiverState, stats: Stats):
    try:
        frame = parse_frame(payload, state.limits)
    except ProtocolError as exc:
        stats.invalid_frames += 1
        stats.last_invalid_frame_error = str(exc)
        return
    if state.accept(frame):
        stats.unique_chunks += 1
    else:
        stats.duplicates += 1


def scan_video(path, state: ReceiverState, stats: Stats, backend="zxing", *, max_frame_pixels=16777216, max_video_frames=1000000):
    """Scan all video frames, retaining every validated chunk before an error."""
    import cv2
    if backend == "zxing":
        import zxingcpp
    elif backend == "opencv":
        detector = cv2.QRCodeDetector()
    else:
        raise ValueError("unknown decoder backend")
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise VideoError(f"cannot open video: {path}")
        width, height = capture.get(cv2.CAP_PROP_FRAME_WIDTH), capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
        if width > 0 and height > 0 and width * height > max_frame_pixels:
            raise VideoError(f"video dimensions exceed pixel limit: {path}")
        video_frames = 0
        while True:
            try:
                ok, pixels = capture.read()
            except cv2.error:
                stats.decode_errors += 1
                raise VideoError(f"video read failed: {path}")
            if not ok:
                break
            video_frames += 1
            if video_frames > max_video_frames:
                raise VideoError(f"video exceeds frame count limit: {path}")
            if pixels is None or len(pixels.shape) < 2 or pixels.shape[0] * pixels.shape[1] > max_frame_pixels:
                raise VideoError(f"decoded frame exceeds pixel limit: {path}")
            stats.frames += 1
            try:
                if backend == "zxing":
                    results = zxingcpp.read_barcodes(pixels, formats=zxingcpp.BarcodeFormat.QRCode)
                    payloads = [result.bytes for result in results if result.valid]
                else:
                    found, decoded, _, _ = detector.detectAndDecodeMulti(pixels)
                    payloads = list(decoded) if found else []
                    if not payloads:
                        single, _, _ = detector.detectAndDecode(pixels)
                        payloads = [single] if single else []
            except Exception:
                # Native cv2.error and zxing exceptions are isolated per frame.
                stats.decode_errors += 1
                continue
            # Session/conflict/persistence errors intentionally escape the decoder boundary.
            for payload in payloads:
                if payload:
                    accept_payload(payload, state, stats)
    finally:
        capture.release()
