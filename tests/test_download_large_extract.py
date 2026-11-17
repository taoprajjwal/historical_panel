import os
from pathlib import Path

from download_large_extract import (
    _recover_allocated_ranges,
    _resplit_pending_ranges,
)


def test_sparse_range_recovery_and_pending_resplit(tmp_path: Path):
    target = tmp_path / "large.dat"
    descriptor = os.open(target, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.ftruncate(descriptor, 32 * 1024)
        os.pwrite(descriptor, b"x" * (8 * 1024), 0)
    finally:
        os.close(descriptor)

    ranges = [(0, 8 * 1024 - 1), (8 * 1024, 32 * 1024 - 1)]
    recovered = _recover_allocated_ranges(target, ranges, set())
    assert recovered == {ranges[0]}

    resplit = _resplit_pending_ranges(ranges, recovered, workers=4)
    assert ranges[0] in resplit
    assert len(resplit) > len(ranges)
    assert sum(end - start + 1 for start, end in resplit) == 32 * 1024
