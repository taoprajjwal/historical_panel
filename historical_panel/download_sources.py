"""Download only the public replication artifacts used by the panel builder.

The Harvard Dataverse deposit is a 1.34 GB ZIP.  It contains many files that
are irrelevant to this project, so this module downloads the archive once,
checks its digest, and extracts a documented allow-list.  The ZIP can be
deleted after extraction or supplied later with ``--archive``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
import urllib.request
import zipfile
from pathlib import Path


DATAVERSE_FILE_ID = 6976313
DATAVERSE_URL = f"https://dataverse.harvard.edu/api/access/datafile/{DATAVERSE_FILE_ID}"
ARCHIVE_SHA256 = "fcc9da9ec8fd971e12f232d4136cd93c147c0a2b95a293dce87124593755dc4f"
ROOT = "BFFPT-QJE2023-Replication"

MEMBERS = (
    f"{ROOT}/README_BFFPT.docx",
    f"{ROOT}/do-files/3_countrymusic.do",
    f"{ROOT}/Data - raw/Census/County/1850_1940_census_county.dta",
    f"{ROOT}/Data - raw/Country Music Radio Stations/CountryRadios.dta",
    f"{ROOT}/Data - raw/Country Music Radio Stations/CountryStationsCounts.dta",
)

RAIL_SNAPSHOT_YEARS = (1873, 1883, 1898, 1912, 1921)
RAIL_EXTENSIONS = ("shp", "shx", "dbf", "prj", "cpg")


def sha256(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    request = urllib.request.Request(url, headers={"User-Agent": "historical-panel/1.0"})
    with urllib.request.urlopen(request) as response, partial.open("wb") as output:
        shutil.copyfileobj(response, output, length=8 * 1024 * 1024)
    partial.replace(destination)


def selected_members(archive: zipfile.ZipFile) -> list[str]:
    available = set(archive.namelist())
    wanted = list(MEMBERS)
    for year in RAIL_SNAPSHOT_YEARS:
        stem = f"{ROOT}/Data - raw/Sequeira et al IV/railroads{year}/railroads{year}"
        wanted.extend(f"{stem}.{extension}" for extension in RAIL_EXTENSIONS)
    required = set(MEMBERS)
    missing = sorted(required - available)
    if missing:
        raise RuntimeError(f"Replication archive is missing required members: {missing}")
    return [member for member in wanted if member in available]


def extract_sources(archive_path: Path, output_dir: Path) -> list[Path]:
    actual = sha256(archive_path)
    if actual != ARCHIVE_SHA256:
        raise RuntimeError(
            f"Unexpected replication archive SHA-256: {actual}; expected {ARCHIVE_SHA256}"
        )
    with zipfile.ZipFile(archive_path) as archive:
        members = selected_members(archive)
        archive.extractall(output_dir, members=members)
    return [output_dir / member for member in members]


def classify_member(name: str, selected: set[str]) -> tuple[str, str, str]:
    suffix = Path(name).suffix.lower()
    if suffix in {".do", ".ado", ".py", ".r", ".m"}:
        artifact_type = "script"
    elif suffix in {".dta", ".csv", ".xlsx", ".xls", ".sav", ".parquet"}:
        artifact_type = "data_table"
    elif suffix in {".shp", ".shx", ".dbf", ".prj", ".cpg"}:
        artifact_type = "spatial_data"
    elif suffix in {".pdf", ".doc", ".docx", ".txt", ".md"}:
        artifact_type = "documentation"
    else:
        artifact_type = "other"
    if "1850_1940_census_county.dta" in name:
        classification = "repeated_historical_measure"
    elif "Country Music Radio Stations" in name:
        classification = "later_outcome"
    elif "railroads" in name.lower():
        classification = "constructed_time_varying_measure"
    elif artifact_type == "script":
        classification = "transformation_code"
    elif artifact_type in {"data_table", "spatial_data"}:
        classification = "candidate_not_imported"
    else:
        classification = "documentation_or_support"
    disposition = "selected_for_release" if name in selected else "not_imported_release_1"
    return artifact_type, classification, disposition


def write_inventory(archive_path: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        selected = set(selected_members(archive))
        with destination.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "member_path", "size_bytes", "compressed_bytes", "extension",
                    "artifact_type", "classification", "disposition",
                ]
            )
            for info in sorted(archive.infolist(), key=lambda item: item.filename):
                if info.is_dir():
                    continue
                artifact_type, classification, disposition = classify_member(
                    info.filename, selected
                )
                writer.writerow(
                    [
                        info.filename, info.file_size, info.compress_size,
                        Path(info.filename).suffix.lower(), artifact_type,
                        classification, disposition,
                    ]
                )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("historical_panel/data/raw"))
    parser.add_argument("--archive", type=Path, help="Use an existing Dataverse ZIP")
    parser.add_argument(
        "--keep-archive",
        action="store_true",
        help="Keep a newly downloaded 1.34 GB archive after extraction",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("historical_panel/replication_inventory.csv"),
        help="Write a complete archive file inventory and release disposition",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    archive = args.archive.resolve() if args.archive else output_dir / "BFFPT-QJE2023-Replication.zip"
    downloaded = args.archive is None
    if downloaded and not archive.exists():
        download(DATAVERSE_URL, archive)
    write_inventory(archive, args.inventory.resolve())
    paths = extract_sources(archive, output_dir)
    print(f"extracted_files={len(paths)}")
    print(f"archive_sha256={sha256(archive)}")
    if downloaded and not args.keep_archive:
        archive.unlink()
        print("archive_removed=true")


if __name__ == "__main__":
    main()
