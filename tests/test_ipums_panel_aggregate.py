from pathlib import Path

import pandas as pd
import pytest

from historical_panel.aggregate_ipums import (
    PARSE_FIELDS,
    aggregate_chunk,
    attach_source_geography,
    consolidate_source_counties,
    harmonize_to_1940,
)


def _people_frame() -> pd.DataFrame:
    rows = []
    for pernum in (1, 2, 3):
        row = {field.lower(): 0 for field in PARSE_FIELDS}
        row.update(
            {
                "year": 1930,
                "sample": 193004,
                "serial": 10,
                "hhwt": 1,
                "stateicp": 45,
                "statefip": 22,
                "countyicp": 750,
                "countynhg": 750,
                "gq": 1,
                "pernum": pernum,
                "perwt": 1,
                "slwt": 0,
                "urban": 2,
                "sex": 1 if pernum != 2 else 2,
                "age": 40 if pernum == 1 else 20,
                "marst": 1,
                "lit": 4,
                "school": 1,
                "labforce": 2,
                "empstat": 1,
                "classwkr": 2,
                "occ1950": 100,
                "ind1950": 105,
                "famsize": 3,
                "nchild": 1,
                "occscore": 20,
                "sei": 30,
                "presgl": 40,
                "erscor50": 50,
                "edscor50": 60,
                "npboss50": 70,
                "incwage": 0,
                "realprop": 100,
                "persprop": 50,
            }
        )
        rows.append(row)
    rows[0].update(
        {
            "bpl": 414,
            "bpld": 41400,
            "occ1950": 57,
            "yrimmig": 1925,
            "citizen": 2,
            "speakeng": 2,
            "ownershp": 1,
            "mortgage": 3,
            "farm": 2,
            "radio30": 2,
            "valueh": 5000,
        }
    )
    rows[1].update({"bpl": 22, "mbpl": 414, "fbpl": 414})
    rows[2].update({"bpl": 22, "mbpl": 22, "fbpl": 25})
    return pd.DataFrame(rows)


def test_ipums_chunk_aggregation_preserves_generations_and_music_measures():
    result = aggregate_chunk(_people_frame())
    assert len(result) == 1
    row = result.iloc[0]
    assert row["population"] == pytest.approx(3)
    assert row["irish_born_count"] == pytest.approx(1)
    assert row["second_generation_irish_count"] == pytest.approx(1)
    assert row["two_irish_parents_count"] == pytest.approx(1)
    assert row["irish_born_musician_music_teacher_count"] == pytest.approx(1)
    assert row["radio_household_count_1930"] == pytest.approx(1)
    assert row["owner_occupied_household_count"] == pytest.approx(1)
    assert row["irish_recent_arrival_10yr_count"] == pytest.approx(1)


def test_ipums_harmonization_allocates_additive_measures(tmp_path: Path):
    source = pd.DataFrame(
        {
            "census_year": [1880],
            "sample": [188003],
            "source_gisjoin": ["source"],
            "source_county_name": ["Source"],
            "source_state_name": ["Example"],
            "source_key_rows": [1],
            "population": [100.0],
            "irish_born_count": [20.0],
        }
    )
    crosswalk = pd.DataFrame(
        {
            "source_year": [1880, 1880],
            "source_gisjoin": ["source", "source"],
            "county_1940_id": ["left", "right"],
            "state_code_1940": ["01", "01"],
            "county_code_1940": ["001", "003"],
            "county_name_1940": ["Left", "Right"],
            "state_name_1940": ["Example", "Example"],
            "source_area_weight": [0.25, 0.75],
        }
    )
    path = tmp_path / "crosswalk.parquet"
    crosswalk.to_parquet(path, index=False)
    result = harmonize_to_1940(source, path)
    assert sorted(result["population"]) == pytest.approx([25, 75])
    assert sorted(result["irish_born_count"]) == pytest.approx([5, 15])


def test_ipums_geography_uses_countynhg_before_1940_and_icpsr_in_1940(monkeypatch):
    columns = [
        "source_gisjoin", "stateicp", "countyicp", "source_county_name",
        "source_state_name", "census_year",
    ]

    def fake_lookup(year, cache_dir=None):
        if year == 1930:
            return pd.DataFrame(
                [["G2207500", 45, 750, "Early", "Example", 1930]],
                columns=columns,
            )
        if year == 1900:
            return pd.DataFrame(
                [["G2200750", 45, 750, "Fallback", "Example", 1900]],
                columns=columns,
            )
        if year == 1940:
            return pd.DataFrame(
                [["G2200750", 45, 750, "Late", "Example", 1940]],
                columns=columns,
            )
        return pd.DataFrame(columns=columns)

    monkeypatch.setattr(
        "historical_panel.aggregate_ipums.historical_county_lookup",
        fake_lookup,
    )
    frame = pd.DataFrame(
        {
            "census_year": [1900, 1930, 1940],
            "sample": [190004, 193004, 194002],
            "stateicp": [45, 45, 45],
            "statefip": [22, 22, 22],
            "countyicp": [750, 9999, 750],
            "countynhg": [0, 2207500, 0],
            "population": [5.0, 10.0, 20.0],
        }
    )
    result = attach_source_geography(frame)
    assert result["source_gisjoin"].tolist() == [
        "G2200750", "G2207500", "G2200750",
    ]
    assert result["source_geography_matched"].all()


def test_source_geography_consolidation_sums_alternative_keys():
    frame = pd.DataFrame(
        {
            "census_year": [1870, 1870],
            "sample": [187003, 187003],
            "stateicp": [98, 98],
            "statefip": [11, 11],
            "countyicp": [11, 12],
            "countynhg": [1100010, 1100010],
            "source_gisjoin": ["G1100010", "G1100010"],
            "source_county_name": ["District of Columbia"] * 2,
            "source_state_name": ["District of Columbia"] * 2,
            "source_geography_matched": [True, True],
            "population": [40.0, 60.0],
            "irish_born_count": [4.0, 6.0],
        }
    )
    result = consolidate_source_counties(frame)
    assert len(result) == 1
    assert result.iloc[0]["source_key_rows"] == 2
    assert result.iloc[0]["population"] == pytest.approx(100)
    assert result.iloc[0]["irish_born_count"] == pytest.approx(10)
