"""Create a recorded-video fixture from a sender manifest, with no camera or network.

Usage: python tests/synthetic_video.py SEND_DIR OUTPUT.mp4 --indices 0,2,1
A .mov output uses the same mp4v codec. Availability depends on the OpenCV build.
"""
import argparse
import json
from pathlib import Path


def make_video(bundle, output, indices=None, repeat=3, fps=6):
    import cv2
    import numpy as np
    bundle, output = Path(bundle), Path(output)
    manifest = json.loads((bundle / "manifest.json").read_text())
    entries = {entry["index"]: entry for entry in manifest["payloads"]}
    indices = list(entries) if indices is None else list(indices)
    if not indices:
        raise ValueError("video needs at least one index")
    images = []
    for index in indices:
        image = cv2.imread(str(bundle / entries[index]["qr"]))
        if image is None:
            raise ValueError(f"cannot read QR image {index}")
        images.append(image)
    side = max(max(img.shape[:2]) for img in images) + 40
    side += side % 2
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*"mp4v"), fps, (side, side))
    if not writer.isOpened():
        raise RuntimeError("OpenCV mp4v video writer is unavailable")
    try:
        for img in images:
            canvas = np.full((side, side, 3), 255, dtype=np.uint8)
            y, x = (side-img.shape[0])//2, (side-img.shape[1])//2
            canvas[y:y+img.shape[0], x:x+img.shape[1]] = img
            for _ in range(repeat):
                writer.write(canvas)
    finally:
        writer.release()
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("video writer produced no output")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--indices", help="comma-separated zero-based indices, in recorded order")
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("repeat must be positive")
    make_video(args.bundle, args.output, None if args.indices is None else [int(i) for i in args.indices.split(",")], args.repeat)
