"""Validate the completed IPUMS county-panel extension and write a report."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
PROCESSED = ROOT / "data/processed"
WORK = ROOT / "data/ipums_aggregate_work"
EXPECTED_SAMPLES = {
    1850: 185002,
    1860: 186003,
    1870: 187003,
    1880: 188003,
    1900: 190004,
    1910: 191004,
    1920: 192003,
    1930: 193004,
    1940: 194002,
}


def validate() -> dict[str, object]:
    manifest = json.loads((WORK / "aggregation_manifest.json").read_text())
    source_keys = pd.read_parquet(PROCESSED / "ipums_source_key_decade.parquet")
    source = pd.read_parquet(PROCESSED / "ipums_source_county_decade.parquet")
    panel = pd.read_parquet(PROCESSED / "ipums_county_decade_1940.parquet")
    allocation = pd.read_csv(PROCESSED / "ipums_geography_allocation_audit.csv")
    catalog = pd.read_csv(ROOT / "ipums_variable_catalog.csv")

    samples = {
        int(year): [int(value) for value in sorted(group["sample"].unique())]
        for year, group in source.groupby("census_year")
    }
    expected_lists = {year: [sample] for year, sample in EXPECTED_SAMPLES.items()}
    unmatched = source_keys[~source_keys["source_geography_matched"]]
    allocation_year = allocation.groupby("census_year").agg(
        source_counties=("source_gisjoin", "size"),
        partial_overlap_count=(
            "allocation_status",
            lambda values: int(values.eq("partial_target_overlap").sum()),
        ),
        no_overlap_count=(
            "allocation_status",
            lambda values: int(values.eq("no_target_overlap").sum()),
        ),
        unallocated_population=("unallocated_population", "sum"),
        unallocated_irish_born=("unallocated_irish_born", "sum"),
    )

    checks = {
        "manifest_complete": bool(manifest["complete"]),
        "raw_complete": bool(manifest["raw_complete"]),
        "source_county_year_unique": not source.duplicated(
            ["census_year", "source_gisjoin"]
        ).any(),
        "harmonized_county_year_unique": not panel.duplicated(
            ["county_1940_id", "census_year"]
        ).any(),
        "expected_samples_only": samples == expected_lists,
        "catalog_matches_panel": catalog["column_name"].tolist() == panel.columns.tolist(),
        "population_nonnegative": bool(panel["population"].ge(0).all()),
        "irish_born_nonnegative": bool(panel["irish_born_count"].ge(0).all()),
        "irish_born_not_above_population": bool(
            panel["irish_born_count"].le(panel["population"] + 1e-8).all()
        ),
        "second_generation_not_above_population": bool(
            panel["second_generation_irish_count"].le(
                panel["population"] + 1e-8
            ).all()
        ),
        "parent_subgroups_not_above_second_generation": bool(
            (
                panel["one_irish_parent_count"]
                + panel["two_irish_parents_count"]
            ).le(panel["second_generation_irish_count"] + 1e-8).all()
        ),
        "all_matched_source_counties_have_crosswalk_overlap": bool(
            allocation["allocation_weight_sum"].gt(0).all()
        ),
        "no_overweight_source_polygons": bool(
            allocation["allocation_weight_sum"].le(1.000001).all()
        ),
    }
    if not all(checks.values()):
        raise RuntimeError(
            "IPUMS validation failed: "
            + ", ".join(name for name, passed in checks.items() if not passed)
        )

    report = {
        "checks": checks,
        "raw": {
            "compressed_bytes": int(manifest["data_size"]),
            "data_sha256": manifest["data_sha256"],
            "ddi_sha256": manifest["ddi_sha256"],
            "record_width": int(manifest["record_width"]),
            "records_scanned": int(manifest["source_rows_scanned"]),
            "records_selected": int(manifest["selected_rows_aggregated"]),
            "chunks": int(manifest["total_chunks"]),
            "gzip_crc_validated": True,
        },
        "outputs": {
            "source_key_rows": len(source_keys),
            "source_county_rows": len(source),
            "harmonized_rows": len(panel),
            "harmonized_columns": len(panel.columns),
            "catalog_rows": len(catalog),
        },
        "samples_by_year": {str(year): values for year, values in samples.items()},
        "harmonized_rows_by_year": {
            str(int(year)): int(count)
            for year, count in panel.groupby("census_year").size().items()
        },
        "harmonized_population_by_year": {
            str(int(year)): float(value)
            for year, value in panel.groupby("census_year")["population"].sum().items()
        },
        "unmatched_source_geographies": {
            "rows": len(unmatched),
            "population": float(unmatched["population"].sum()),
            "population_share": float(
                unmatched["population"].sum() / source_keys["population"].sum()
            ),
        },
        "allocation_by_year": {
            str(int(year)): {
                key: float(value) if key.startswith("unallocated") else int(value)
                for key, value in row.items()
            }
            for year, row in allocation_year.to_dict("index").items()
        },
    }
    path = PROCESSED / "ipums_validation_report.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


if __name__ == "__main__":
    output = validate()
    print(json.dumps(output, indent=2, sort_keys=True))
