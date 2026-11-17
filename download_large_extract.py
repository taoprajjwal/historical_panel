"""Download a large authenticated file with validated parallel byte ranges.

This helper is intended for short-lived download URLs.  It stores no URL in
the resume manifest, only its SHA-256 fingerprint, and writes ranges directly
to their final offsets with ``os.pwrite``.  A completed-range manifest makes
the transfer resumable without retaining a second copy of the download.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path


CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)")


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _request(url: str, start: int, end: int):
    return urllib.request.urlopen(
        urllib.request.Request(
            url,
            headers={
                "Range": f"bytes={start}-{end}",
                "User-Agent": "historical-panel-ipums-downloader/1.0",
            },
        ),
        timeout=120,
    )


def probe_size(url: str) -> int:
    with _request(url, 0, 0) as response:
        match = CONTENT_RANGE.fullmatch(response.headers.get("Content-Range", ""))
        if response.status != 206 or match is None:
            raise RuntimeError("server did not honor a one-byte range request")
        first, last, total = map(int, match.groups())
        if (first, last) != (0, 0):
            raise RuntimeError("server returned an unexpected probe range")
        response.read()
        return total


def _ranges(start: int, total: int, count: int) -> list[tuple[int, int]]:
    if start >= total:
        return []
    chunk_size = math.ceil((total - start) / count)
    return [
        (offset, min(total - 1, offset + chunk_size - 1))
        for offset in range(start, total, chunk_size)
    ]


def _recover_allocated_ranges(
    target: Path,
    ranges: list[tuple[int, int]],
    completed: set[tuple[int, int]],
) -> set[tuple[int, int]]:
    """Recover fully written ranges from sparse-file allocation metadata.

    This downloader creates its target with ``ftruncate``, so unwritten space
    remains a filesystem hole.  A range containing no hole was fully written
    even if a previous process stopped before persisting its completion marker.
    """

    if not hasattr(os, "SEEK_HOLE"):
        return completed
    descriptor = os.open(target, os.O_RDONLY)
    recovered = set(completed)
    try:
        for start, end in ranges:
            pair = (start, end)
            if pair in recovered:
                continue
            try:
                data_start = os.lseek(descriptor, start, os.SEEK_DATA)
                if data_start != start:
                    continue
                hole_start = os.lseek(descriptor, start, os.SEEK_HOLE)
            except OSError:
                continue
            if hole_start > end:
                recovered.add(pair)
    finally:
        os.close(descriptor)
    return recovered


def _resplit_pending_ranges(
    ranges: list[tuple[int, int]],
    completed: set[tuple[int, int]],
    workers: int,
) -> list[tuple[int, int]]:
    pending = [pair for pair in ranges if pair not in completed]
    if not pending or len(pending) >= workers:
        return ranges
    pending_bytes = sum(end - start + 1 for start, end in pending)
    piece_size = max(1, math.ceil(pending_bytes / workers))
    if max(end - start + 1 for start, end in pending) <= piece_size:
        return ranges
    split: list[tuple[int, int]] = []
    for start, end in pending:
        split.extend(
            (offset, min(end, offset + piece_size - 1))
            for offset in range(start, end + 1, piece_size)
        )
    return sorted(completed) + split


def download(
    *,
    url: str,
    target: Path,
    workers: int,
    retries: int,
) -> None:
    if workers <= 0 or retries <= 0:
        raise ValueError("workers and retries must be positive")
    total = probe_size(url)
    split_url = urllib.parse.urlsplit(url)
    url_identity = urllib.parse.urlunsplit(
        (split_url.scheme, split_url.netloc, split_url.path, "", "")
    )
    url_identity_hash = hashlib.sha256(url_identity.encode()).hexdigest()
    manifest_path = target.with_suffix(target.suffix + ".download.json")

    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        stored_identity = manifest.get("url_identity_sha256")
        legacy_manifest = stored_identity is None and "url_sha256" in manifest
        if manifest["total_bytes"] != total or (
            not legacy_manifest and stored_identity != url_identity_hash
        ):
            raise RuntimeError("download manifest does not match this URL/file")
        if legacy_manifest:
            if Path(split_url.path).name != target.name:
                raise RuntimeError("legacy manifest target does not match URL filename")
            manifest["url_identity_sha256"] = url_identity_hash
            manifest.pop("url_sha256", None)
        prefix_bytes = int(manifest["prefix_bytes"])
        ranges = [tuple(pair) for pair in manifest["ranges"]]
        completed = {tuple(pair) for pair in manifest["completed_ranges"]}
        if target.stat().st_size != total:
            raise RuntimeError("preallocated target size does not match manifest")
        recovered = _recover_allocated_ranges(target, ranges, completed)
        if recovered != completed:
            print(
                f"recovered_completed_ranges={len(recovered) - len(completed)}",
                flush=True,
            )
            completed = recovered
            manifest["completed_ranges"] = sorted(completed)
            _atomic_json(manifest_path, manifest)
        resplit = _resplit_pending_ranges(ranges, completed, workers)
        if resplit != ranges:
            ranges = resplit
            manifest["ranges"] = ranges
            completed = _recover_allocated_ranges(target, ranges, completed)
            manifest["completed_ranges"] = sorted(completed)
            _atomic_json(manifest_path, manifest)
            print(f"resplit_total_ranges={len(ranges)}", flush=True)
    else:
        prefix_bytes = target.stat().st_size if target.exists() else 0
        if prefix_bytes > total:
            raise RuntimeError("existing partial file is larger than remote file")
        ranges = _ranges(prefix_bytes, total, workers)
        completed: set[tuple[int, int]] = set()
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(target, os.O_RDWR | os.O_CREAT, 0o664)
        try:
            os.ftruncate(descriptor, total)
        finally:
            os.close(descriptor)
        manifest = {
            "url_identity_sha256": url_identity_hash,
            "total_bytes": total,
            "prefix_bytes": prefix_bytes,
            "ranges": ranges,
            "completed_ranges": [],
            "complete": False,
        }
        _atomic_json(manifest_path, manifest)

    pending = [pair for pair in ranges if pair not in completed]
    if not pending:
        manifest["complete"] = True
        _atomic_json(manifest_path, manifest)
        print(f"complete bytes={total:,}", flush=True)
        return

    descriptor = os.open(target, os.O_RDWR)
    lock = threading.Lock()
    started = time.monotonic()
    bytes_complete = prefix_bytes + sum(end - start + 1 for start, end in completed)

    def fetch(pair: tuple[int, int]) -> tuple[int, int]:
        start, end = pair
        expected = end - start + 1
        for attempt in range(1, retries + 1):
            try:
                with _request(url, start, end) as response:
                    match = CONTENT_RANGE.fullmatch(
                        response.headers.get("Content-Range", "")
                    )
                    if response.status != 206 or match is None:
                        raise RuntimeError("range request was not honored")
                    got_start, got_end, got_total = map(int, match.groups())
                    if (got_start, got_end, got_total) != (start, end, total):
                        raise RuntimeError("server returned the wrong byte range")
                    position = start
                    while block := response.read(1024 * 1024):
                        os.pwrite(descriptor, block, position)
                        position += len(block)
                    if position - start != expected:
                        raise RuntimeError(
                            f"short range {start}-{end}: {position - start} != {expected}"
                        )
                return pair
            except Exception:
                if attempt == retries:
                    raise
                time.sleep(min(30, 2**attempt))
        raise AssertionError("unreachable")

    try:
        failures: list[tuple[tuple[int, int], str]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fetch, pair): pair for pair in pending}
            for future in concurrent.futures.as_completed(futures):
                requested_pair = futures[future]
                try:
                    pair = future.result()
                except Exception as error:
                    failures.append((requested_pair, repr(error)))
                    print(
                        f"failed_range={requested_pair[0]}-{requested_pair[1]} "
                        f"error={error!r}",
                        flush=True,
                    )
                    continue
                with lock:
                    completed.add(pair)
                    bytes_complete += pair[1] - pair[0] + 1
                    manifest["completed_ranges"] = sorted(completed)
                    _atomic_json(manifest_path, manifest)
                    elapsed = max(time.monotonic() - started, 1e-9)
                    transferred = bytes_complete - prefix_bytes
                    rate = transferred / elapsed
                    remaining = total - bytes_complete
                    eta = remaining / rate if rate else float("inf")
                    print(
                        f"ranges={len(completed)}/{len(ranges)} "
                        f"bytes={bytes_complete:,}/{total:,} "
                        f"rate_mib_s={rate / 2**20:.2f} eta_minutes={eta / 60:.1f}",
                        flush=True,
                    )
    finally:
        os.close(descriptor)

    if failures:
        raise RuntimeError(
            f"{len(failures)} ranges failed; rerun with a refreshed URL to resume"
        )

    manifest["complete"] = True
    _atomic_json(manifest_path, manifest)
    print(f"complete bytes={total:,}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--retries", type=int, default=8)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    download(
        url=arguments.url,
        target=arguments.target,
        workers=arguments.workers,
        retries=arguments.retries,
    )
