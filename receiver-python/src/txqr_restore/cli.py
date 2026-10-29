"""Command line receiver, with machine-readable replay reports."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

from .protocol import Limits, MAX_FRAME
from .scanner import Stats, accept_payload, scan_video
from .state import Incomplete, ReceiverState, StateError, atomic_write


def parser():
    p = argparse.ArgumentParser(description="Restore one TXQRT1 session from one or more QR videos. Hashes are not authentication.")
    p.add_argument("videos", nargs="*", metavar="VIDEO")
    p.add_argument("--state", required=True, type=Path, help="durable directory for one session (reuse to resume)")
    p.add_argument("--output", required=True, type=Path, help="output directory; verified metadata supplies the basename")
    p.add_argument("--payload-file", action="append", type=Path, default=[], help="newline ASCII payloads, useful for diagnostics; repeatable")
    p.add_argument("--backend", choices=("zxing", "opencv"), default="zxing")
    p.add_argument("--overwrite", action="store_true", help="explicitly replace an existing output file")
    p.add_argument("--report", type=Path, help="atomically write JSON report (also printed to stdout)")
    p.add_argument("--max-raw-size", type=int, default=64 * 1024 * 1024)
    p.add_argument("--max-wire-size", type=int, default=64 * 1024 * 1024)
    p.add_argument("--max-chunks", type=int, default=100000)
    p.add_argument("--max-chunk-size", type=int, default=4096)
    p.add_argument("--max-frame-pixels", type=int, default=16777216, help="at most 16777216 pixels per decoded video frame")
    p.add_argument("--max-video-frames", type=int, default=1000000, help="at most 1000000 frames per input video")
    return p


def scan_payload_file(path: Path, state: ReceiverState, stats: Stats):
    with path.open("rb") as stream:
        while True:
            line = stream.readline(MAX_FRAME + 3)
            if not line:
                return
            if len(line) > MAX_FRAME + 2 or (not line.endswith(b"\n") and len(line) > MAX_FRAME):
                stats.invalid_frames += 1
                stats.last_invalid_frame_error = "payload line exceeds maximum frame length"
                # Drain one overlong line in bounded pieces.
                while line and not line.endswith(b"\n"):
                    line = stream.readline(MAX_FRAME + 3)
                continue
            accept_payload(line.rstrip(b"\r\n"), state, stats)


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    stats = Stats()
    state = None
    output = None
    error = None
    status = "error"
    report_safe = not bool(args.report)
    exit_code = 1
    try:
        state_path, output_path = args.state.resolve(), args.output.resolve()
        if args.report:
            report_path = args.report.resolve()
            report_lexical = Path(os.path.abspath(args.report))
            for protected in (args.state, args.output):
                resolved, lexical = protected.resolve(), Path(os.path.abspath(protected))
                if (report_path == resolved or resolved in report_path.parents
                        or report_lexical == lexical or lexical in report_lexical.parents):
                    raise ValueError("report must be outside the state and output directories")
            for source in (*args.payload_file, *(Path(path) for path in args.videos)):
                same_file = False
                try:
                    same_file = args.report.samefile(source)
                except OSError:
                    pass
                if report_path == source.resolve() or same_file:
                    raise ValueError("report must not overwrite an input file")
            report_safe = True
        limits = Limits(args.max_raw_size, args.max_wire_size, args.max_chunks, args.max_chunk_size)
        if not 1 <= args.max_frame_pixels <= 16777216 or not 1 <= args.max_video_frames <= 1000000:
            raise ValueError("video limits must be positive and cannot exceed built-in maxima")
        if output_path == state_path or state_path in output_path.parents:
            raise ValueError("output directory must not be inside the state directory")
        state = ReceiverState(args.state, limits)
        with state.locked():
            for path in args.payload_file:
                scan_payload_file(path, state, stats)
            for path in args.videos:
                scan_video(path, state, stats, args.backend,
                           max_frame_pixels=args.max_frame_pixels, max_video_frames=args.max_video_frames)
            try:
                output = str(state.restore(args.output, args.overwrite))
                status, exit_code = "complete", 0
            except Incomplete as exc:
                status, exit_code, error = "incomplete", 2, str(exc)
    except (Exception, KeyboardInterrupt) as exc:
        # Always preserve state and emit a final diagnostic, including backend/open failures.
        error = f"{type(exc).__name__}: {exc}"
    report = {"protocol": "TXQRT1", "status": status, "session": state.session if state else None,
              "missing": state.missing if state else [],
              "chunks_present": len(state.chunks) if state else 0,
              "chunks_total": state.metadata["chunks"] if state and state.metadata else None,
              **asdict(stats), "output": output, "error": error}
    encoded = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if args.report and report_safe:
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(args.report, encoded)
        except OSError as exc:
            report["status"] = "error"
            report["error"] = f"cannot write report: {exc}"
            encoded = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            print(report["error"], file=sys.stderr)
            exit_code = 1
    print(encoded.decode("utf-8"), end="")
    if error:
        print(error, file=sys.stderr)
    return exit_code
