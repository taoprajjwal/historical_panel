"""Stream a fixed-width IPUMS USA extract into linkage-oriented Parquet.

The converter intentionally reads ``.dat.gz`` directly.  It never creates an
uncompressed copy of the source file.  The DDI XML is the source of truth for
column positions and data types, and is validated against every input block.

Two datasets are produced:

``people``
    A narrow record for every selected person.  This retains household keys
    and relationship fields for later household-context checks.

``irish_candidates``
    Irish-born people with usable names, plus precomputed blocking features.
    This is partitioned by birth decade as well as census year and sample.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from historical_linkage import normalize_name, nysiis, split_given_name


DDI_NAMESPACE = {"ddi": "ddi:codebook:2_5"}

# This deliberately omits the 102-character occupation string.  It keeps the
# demographic, migration, name, and household fields useful for linkage while
# materially reducing the full-count Parquet footprint.
DEFAULT_FIELDS = (
    "YEAR",
    "SAMPLE",
    "SERIAL",
    "HHWT",
    "GQ",
    "PERNUM",
    "PERWT",
    "RELATE",
    "RELATED",
    "IMPREL",
    "SEX",
    "AGE",
    "BIRTHYR",
    "MARST",
    "RACE",
    "RACED",
    "BPL",
    "BPLD",
    "MBPL",
    "MBPLD",
    "FBPL",
    "FBPLD",
    "NATIVITY",
    "CITIZEN",
    "YRIMMIG",
    "YRSUSA1",
    "YRSUSA2",
    "MTONGUE",
    "MTONGUED",
    "VERSIONHIST",
    "NAMELAST",
    "NAMEFRST",
    "HISTID",
    "SURSIM",
    "PERNUM80",
)


@dataclass(frozen=True)
class DDIVariable:
    """The fixed-width information needed to parse one DDI variable."""

    name: str
    start: int
    end: int
    width: int
    kind: str
    decimals: int
    label: str


@dataclass(frozen=True)
class DDIExtract:
    """Validated metadata for an IPUMS fixed-width extract."""

    ddi_id: str
    data_filename: str
    charset: str
    record_width: int
    variables: tuple[DDIVariable, ...]


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def read_ddi(path: Path) -> DDIExtract:
    """Read and structurally validate an IPUMS DDI 2.5 codebook."""

    root = ET.parse(path).getroot()
    expected_root = "{ddi:codebook:2_5}codeBook"
    if root.tag != expected_root:
        raise ValueError(f"expected DDI 2.5 root {expected_root!r}, found {root.tag!r}")

    filename = root.findtext(
        ".//ddi:fileDscr/ddi:fileTxt/ddi:fileName", namespaces=DDI_NAMESPACE
    )
    file_type = root.find(
        ".//ddi:fileDscr/ddi:fileTxt/ddi:fileType", DDI_NAMESPACE
    )
    charset = file_type.get("charset", "ISO-8859-1") if file_type is not None else "ISO-8859-1"
    variables: list[DDIVariable] = []
    for node in root.findall(".//ddi:dataDscr/ddi:var", DDI_NAMESPACE):
        location = node.find("ddi:location", DDI_NAMESPACE)
        variable_format = node.find("ddi:varFormat", DDI_NAMESPACE)
        if location is None:
            continue
        start = int(location.get("StartPos", "0"))
        end = int(location.get("EndPos", "0"))
        width = int(location.get("width", "0"))
        if start < 1 or end < start or end - start + 1 != width:
            raise ValueError(f"invalid location for variable {node.get('name')}")
        variables.append(
            DDIVariable(
                name=node.get("name", ""),
                start=start,
                end=end,
                width=width,
                kind=(variable_format.get("type", "numeric") if variable_format is not None else "numeric"),
                decimals=int(node.get("dcml", "0")),
                label=node.findtext("ddi:labl", default="", namespaces=DDI_NAMESPACE),
            )
        )

    if not variables:
        raise ValueError("DDI contains no fixed-width variables")
    variables.sort(key=lambda value: value.start)
    if variables[0].start != 1:
        raise ValueError("the first DDI variable does not start at column 1")
    for left, right in zip(variables, variables[1:]):
        if left.end + 1 != right.start:
            raise ValueError(
                f"DDI locations are not contiguous between {left.name} and {right.name}"
            )

    return DDIExtract(
        ddi_id=root.get("ID", ""),
        data_filename=filename or "",
        charset=charset,
        record_width=variables[-1].end,
        variables=tuple(variables),
    )


def validate_ddi_for_data(ddi: DDIExtract, data_path: Path, sample_records: int = 1_000) -> None:
    """Check the DDI filename and record width against the compressed data."""

    expected_name = data_path.name.removesuffix(".gz")
    if ddi.data_filename != expected_name:
        raise ValueError(
            f"DDI describes {ddi.data_filename!r}, but data file is {expected_name!r}"
        )
    with gzip.open(data_path, "rb") as handle:
        for index in range(sample_records):
            line = handle.readline()
            if not line:
                break
            record = line.rstrip(b"\r\n")
            if len(record) != ddi.record_width:
                raise ValueError(
                    f"record {index + 1} has width {len(record)}, expected {ddi.record_width}"
                )


def _integer_dtype(width: int) -> str:
    if width <= 4:
        return "Int16"
    if width <= 9:
        return "Int32"
    return "Int64"


def parse_fixed_width_block(
    block: bytes,
    *,
    ddi: DDIExtract,
    fields: Iterable[str] = DEFAULT_FIELDS,
) -> pd.DataFrame:
    """Parse a newline-aligned byte block using zero-copy strided views."""

    stride = ddi.record_width + 1
    if len(block) % stride:
        raise ValueError(
            f"input block has {len(block)} bytes, which is not a multiple of record stride {stride}"
        )
    row_count = len(block) // stride
    if not row_count:
        return pd.DataFrame()
    terminators = np.frombuffer(block, dtype=np.uint8)[ddi.record_width::stride]
    if not np.all(terminators == ord("\n")):
        raise ValueError("input is not a fixed-width LF-delimited file matching the DDI")

    requested = tuple(dict.fromkeys(fields))
    specifications = {variable.name: variable for variable in ddi.variables}
    missing = set(requested).difference(specifications)
    if missing:
        raise KeyError(f"DDI is missing requested fields: {sorted(missing)}")

    columns: dict[str, pd.Series] = {}
    for name in requested:
        variable = specifications[name]
        view = np.ndarray(
            shape=(row_count,),
            dtype=f"S{variable.width}",
            buffer=block,
            offset=variable.start - 1,
            strides=(stride,),
        )
        # The DDI declares ISO-8859-1.  Copying each strided field here avoids
        # retaining the complete decompressed block after a chunk is written.
        values = np.char.strip(np.char.decode(view.copy(), ddi.charset))
        output_name = name.lower()
        if variable.kind == "character":
            columns[output_name] = pd.Series(values, dtype="string")
            continue
        numeric = pd.to_numeric(pd.Series(values), errors="coerce")
        if variable.decimals:
            columns[output_name] = numeric.astype("Float64") / (10 ** variable.decimals)
        else:
            columns[output_name] = numeric.astype(_integer_dtype(variable.width))
    return pd.DataFrame(columns)


def add_linkage_features(people: pd.DataFrame) -> pd.DataFrame:
    """Create stable identifiers and blocking fields for parsed IPUMS rows."""

    result = people.copy()
    histid = result["histid"].fillna("").str.strip()
    fallback = (
        result["sample"].astype("string")
        + ":"
        + result["serial"].astype("string")
        + ":"
        + result["pernum"].astype("string")
    )
    result["record_id"] = histid.mask(histid.eq(""), fallback)
    result["first_raw"] = result["namefrst"].fillna("").str.strip()
    result["last_raw"] = result["namelast"].fillna("").str.strip()
    parsed_given = result["first_raw"].map(split_given_name)
    result["first_norm"] = parsed_given.map(lambda value: value[0])
    result["middle_initial"] = parsed_given.map(lambda value: value[1])
    result["last_norm"] = result["last_raw"].map(normalize_name)
    result["first_nysiis"] = result["first_norm"].map(nysiis)
    result["last_nysiis"] = result["last_norm"].map(nysiis)
    result["first_initial"] = result["first_norm"].str[:1]
    result["last_initial"] = result["last_norm"].str[:1]
    result["census_year"] = result["year"].astype("Int16")
    result["birthplace"] = result["bpl"].astype("Int16")

    stated_birth_year = pd.to_numeric(result["birthyr"], errors="coerce")
    age = pd.to_numeric(result["age"], errors="coerce")
    age_based = result["year"] - age
    valid_stated = stated_birth_year.between(result["year"] - 125, result["year"])
    valid_age = age.between(0, 125)
    birth_year = stated_birth_year.where(valid_stated, age_based.where(valid_age))
    result["birth_year"] = birth_year.round().astype("Int16")
    result["birth_decade"] = (
        result["birth_year"].floordiv(10).mul(10).fillna(-1).astype("Int16")
    )
    return result


def irish_candidate_index(people: pd.DataFrame) -> pd.DataFrame:
    """Return Irish-born rows with the fields consumed by the matcher."""

    # Filter before the Python-level name transforms.  Irish-born people are a
    # small fraction of the complete census, so this is important for full-run
    # conversion speed.
    irish = people[
        people["bpl"].eq(414)
        & people["namefrst"].fillna("").str.strip().ne("")
        & people["namelast"].fillna("").str.strip().ne("")
    ].copy()
    featured = add_linkage_features(irish)
    candidates = featured[
        featured["birthplace"].eq(414)
        & featured["first_norm"].ne("")
        & featured["last_norm"].ne("")
        & featured["birth_year"].notna()
    ].copy()
    columns = [
        "record_id",
        "census_year",
        "year",
        "sample",
        "serial",
        "pernum",
        "histid",
        "sex",
        "age",
        "birth_year",
        "birth_decade",
        "birthplace",
        "bpld",
        "nativity",
        "citizen",
        "yrimmig",
        "relate",
        "related",
        "marst",
        "first_raw",
        "last_raw",
        "first_norm",
        "middle_initial",
        "last_norm",
        "first_nysiis",
        "last_nysiis",
        "first_initial",
        "last_initial",
    ]
    return candidates[columns].reset_index(drop=True)


def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _write_parquet_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    table = pa.Table.from_pandas(frame, preserve_index=False)
    pq.write_table(
        table,
        temporary,
        compression="zstd",
        compression_level=3,
        use_dictionary=True,
        write_statistics=True,
        row_group_size=250_000,
    )
    os.replace(temporary, path)


def _write_people_partitions(frame: pd.DataFrame, root: Path, chunk_index: int) -> None:
    for (year, sample), group in frame.groupby(["year", "sample"], sort=False, dropna=False):
        path = (
            root
            / "people"
            / f"year={int(year)}"
            / f"sample={int(sample)}"
            / f"part-{chunk_index:06d}.parquet"
        )
        _write_parquet_atomic(group.reset_index(drop=True), path)


def _write_candidate_partitions(frame: pd.DataFrame, root: Path, chunk_index: int) -> None:
    for (year, sample, decade), group in frame.groupby(
        ["year", "sample", "birth_decade"], sort=False, dropna=False
    ):
        path = (
            root
            / "irish_candidates"
            / f"year={int(year)}"
            / f"sample={int(sample)}"
            / f"birth_decade={int(decade)}"
            / f"part-{chunk_index:06d}.parquet"
        )
        _write_parquet_atomic(group.reset_index(drop=True), path)


def _format_duration(seconds: float | None) -> str:
    if seconds is None or not np.isfinite(seconds):
        return "unknown"
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def convert_extract(
    *,
    data_path: Path,
    ddi_path: Path,
    output_dir: Path,
    chunk_rows: int = 500_000,
    samples: set[int] | None = None,
    years: set[int] | None = None,
    stop_after_selected: bool = False,
    resume: bool = False,
    status_seconds: float = 5.0,
) -> dict[str, object]:
    """Convert selected records and return the final conversion manifest."""

    if chunk_rows <= 0:
        raise ValueError("chunk_rows must be positive")
    ddi = read_ddi(ddi_path)
    validate_ddi_for_data(ddi, data_path)
    ddi_hash = sha256_file(ddi_path)
    source_size = data_path.stat().st_size
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "conversion_manifest.json"

    signature = {
        "source_path": str(data_path.resolve()),
        "source_size": source_size,
        "ddi_path": str(ddi_path.resolve()),
        "ddi_sha256": ddi_hash,
        "ddi_id": ddi.ddi_id,
        "data_filename": ddi.data_filename,
        "record_width": ddi.record_width,
        "chunk_rows": chunk_rows,
        "selected_samples": sorted(samples) if samples else None,
        "selected_years": sorted(years) if years else None,
        "stop_after_selected": stop_after_selected,
    }
    if manifest_path.exists():
        if not resume:
            raise FileExistsError(
                f"{manifest_path} already exists; pass --resume or choose another output directory"
            )
        manifest = json.loads(manifest_path.read_text())
        for key, expected in signature.items():
            if manifest.get(key) != expected:
                raise ValueError(
                    f"resume manifest mismatch for {key}: {manifest.get(key)!r} != {expected!r}"
                )
    else:
        manifest = {
            **signature,
            "complete": False,
            "completed_chunks": [],
            "completed_source_samples": [],
            "source_rows_scanned": 0,
            "people_rows_written": 0,
            "irish_candidate_rows_written": 0,
            "people_rows_by_sample": {},
            "irish_candidates_by_sample": {},
            "elapsed_seconds": 0.0,
        }
        _write_json_atomic(manifest_path, manifest)

    completed_chunks = set(int(value) for value in manifest["completed_chunks"])
    completed_source_samples = set(
        int(value) for value in manifest.get("completed_source_samples", [])
    )
    stride = ddi.record_width + 1
    started = time.perf_counter()
    last_status = started
    seen_selected = False
    stopped_after_selection = False
    last_source_sample: int | None = None

    with gzip.open(data_path, "rb") as handle:
        chunk_index = 0
        while block := handle.read(stride * chunk_rows):
            if len(block) % stride:
                raise ValueError("final decompressed block ends inside a fixed-width record")
            # During resume, only YEAR/SAMPLE are needed while replaying the
            # sequential gzip stream through chunks already committed.  This
            # makes recovery far cheaper than reparsing every selected field.
            parse_fields = ("YEAR", "SAMPLE") if chunk_index in completed_chunks else DEFAULT_FIELDS
            parsed = parse_fixed_width_block(block, ddi=ddi, fields=parse_fields)
            source_sample_sequence = [
                int(value) for value in parsed["sample"].drop_duplicates().tolist()
            ]
            newly_completed_samples: list[int] = []
            if last_source_sample is not None and source_sample_sequence[0] != last_source_sample:
                newly_completed_samples.append(last_source_sample)
            newly_completed_samples.extend(source_sample_sequence[:-1])
            last_source_sample = source_sample_sequence[-1]
            selected = pd.Series(True, index=parsed.index)
            if samples:
                selected &= parsed["sample"].isin(samples)
            if years:
                selected &= parsed["year"].isin(years)
            selected_count = int(selected.sum())
            if selected_count:
                seen_selected = True

            if chunk_index not in completed_chunks:
                people = parsed[selected].reset_index(drop=True)
                candidates = irish_candidate_index(people) if not people.empty else pd.DataFrame()
                if not people.empty:
                    _write_people_partitions(people, output_dir, chunk_index)
                if not candidates.empty:
                    _write_candidate_partitions(candidates, output_dir, chunk_index)

                manifest["source_rows_scanned"] = int(manifest["source_rows_scanned"]) + len(parsed)
                manifest["people_rows_written"] = int(manifest["people_rows_written"]) + len(people)
                manifest["irish_candidate_rows_written"] = int(
                    manifest["irish_candidate_rows_written"]
                ) + len(candidates)
                for sample, count in people.groupby("sample").size().items():
                    key = str(int(sample))
                    manifest["people_rows_by_sample"][key] = int(
                        manifest["people_rows_by_sample"].get(key, 0)
                    ) + int(count)
                if not candidates.empty:
                    for sample, count in candidates.groupby("sample").size().items():
                        key = str(int(sample))
                        manifest["irish_candidates_by_sample"][key] = int(
                            manifest["irish_candidates_by_sample"].get(key, 0)
                        ) + int(count)
                completed_chunks.add(chunk_index)
                manifest["completed_chunks"] = sorted(completed_chunks)
                manifest["elapsed_seconds"] = float(manifest["elapsed_seconds"]) + (
                    time.perf_counter() - started
                )
                # Reset the interval accumulated into the persisted elapsed time.
                started = time.perf_counter()

            for completed_sample in newly_completed_samples:
                if completed_sample not in completed_source_samples:
                    completed_source_samples.add(completed_sample)
                    print(f"completed_source_sample={completed_sample}", flush=True)
            manifest["completed_source_samples"] = sorted(completed_source_samples)
            _write_json_atomic(manifest_path, manifest)

            selection_finished = bool(
                stop_after_selected
                and seen_selected
                and (selected_count == 0 or not bool(selected.iloc[-1]))
            )
            now = time.perf_counter()
            if now - last_status >= status_seconds or selection_finished:
                compressed_position = min(handle.fileobj.tell(), source_size)
                elapsed_total = float(manifest["elapsed_seconds"]) + (now - started)
                rate = compressed_position / elapsed_total if elapsed_total > 0 else 0
                eta = (source_size - compressed_position) / rate if rate > 0 else None
                print(
                    f"scanned={manifest['source_rows_scanned']:,} "
                    f"people={manifest['people_rows_written']:,} "
                    f"irish_candidates={manifest['irish_candidate_rows_written']:,} "
                    f"current_sample={int(parsed['sample'].iloc[-1])} "
                    f"compressed={compressed_position / source_size:.1%} "
                    f"full_scan_eta={_format_duration(eta)}",
                    flush=True,
                )
                last_status = now
            if selection_finished:
                stopped_after_selection = True
                break
            chunk_index += 1

    if not stopped_after_selection and last_source_sample is not None:
        if last_source_sample not in completed_source_samples:
            completed_source_samples.add(last_source_sample)
            print(f"completed_source_sample={last_source_sample}", flush=True)
    manifest["completed_source_samples"] = sorted(completed_source_samples)
    manifest["complete"] = not stopped_after_selection
    manifest["stopped_after_selected_block"] = stopped_after_selection
    manifest["elapsed_seconds"] = float(manifest["elapsed_seconds"]) + (
        time.perf_counter() - started
    )
    _write_json_atomic(manifest_path, manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True, help="IPUMS .dat.gz file")
    parser.add_argument("--ddi", type=Path, required=True, help="matching IPUMS DDI .xml file")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--chunk-rows", type=int, default=500_000)
    parser.add_argument("--samples", nargs="*", type=int)
    parser.add_argument("--years", nargs="*", type=int)
    parser.add_argument(
        "--stop-after-selected",
        action="store_true",
        help="stop after leaving a contiguous selected block in this sample-sorted extract",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--status-seconds", type=float, default=5.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = convert_extract(
        data_path=args.data,
        ddi_path=args.ddi,
        output_dir=args.output_dir,
        chunk_rows=args.chunk_rows,
        samples=set(args.samples) if args.samples else None,
        years=set(args.years) if args.years else None,
        stop_after_selected=args.stop_after_selected,
        resume=args.resume,
        status_seconds=args.status_seconds,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
