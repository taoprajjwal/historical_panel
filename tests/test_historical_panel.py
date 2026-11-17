import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from historical_panel.build_panel import (
    CENSUS_YEARS,
    add_market_potential,
    build_crosswalk,
    clean_source_keys,
    validate_panel,
)


def test_years_exclude_destroyed_1890_census():
    assert CENSUS_YEARS == (1850, 1860, 1870, 1880, 1900, 1910, 1920, 1930, 1940)
    assert 1890 not in CENSUS_YEARS


def test_source_key_cleanup_prefers_matching_fips_and_populated_rows():
    source = pd.DataFrame(
        {
            "year": [1860.0, 1860.0, 1870.0, 1870.0],
            "gisjoin": ["G4100650", "G4100650", "G0951755", "G0951755"],
            "fips": [41061.1, 41065.0, float("nan"), float("nan")],
            "totpop": [1250.0, 1689.0, float("nan"), 42.0],
        }
    )
    result = clean_source_keys(source)
    assert len(result) == 2
    assert result.loc[result["gisjoin"].eq("G4100650"), "fips"].item() == 41065.0
    assert result.loc[result["gisjoin"].eq("G0951755"), "totpop"].item() == 42.0


def test_area_crosswalk_weights_source_polygons_to_one():
    source = gpd.GeoDataFrame(
        {"GISJOIN": ["source"]}, geometry=[box(0, 0, 2, 1)], crs="EPSG:5070"
    )
    target = gpd.GeoDataFrame(
        {
            "GISJOIN": ["left", "right"],
            "STATE": ["01", "01"],
            "COUNTY": ["001", "003"],
            "NHGISNAM": ["Left", "Right"],
            "STATENAM": ["Example", "Example"],
        },
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
        crs="EPSG:5070",
    )
    crosswalk = build_crosswalk(source, target, 1880)
    assert crosswalk["source_area_weight"].sum() == pytest.approx(1.0)
    assert sorted(crosswalk["source_area_weight"]) == pytest.approx([0.5, 0.5])
    assert set(crosswalk["crosswalk_quality"]) == {"good"}


def test_market_potential_excludes_own_population():
    targets = gpd.GeoDataFrame(
        {"GISJOIN": ["a", "b"]},
        geometry=[box(0, 0, 1_000, 1_000), box(10_000, 0, 11_000, 1_000)],
        crs="EPSG:5070",
    )
    panel = pd.DataFrame(
        {
            "county_1940_id": ["a", "b"],
            "census_year": [1900, 1900],
            "population": [100.0, 200.0],
        }
    )
    result = add_market_potential(panel, targets)
    assert result.loc[result["county_1940_id"].eq("a"), "market_potential_persons_per_km"].item() == pytest.approx(20.0)
    assert result.loc[result["county_1940_id"].eq("b"), "market_potential_persons_per_km"].item() == pytest.approx(10.0)


def test_validation_rejects_duplicate_keys_and_1890():
    panel = pd.DataFrame(
        {
            "county_1940_id": ["a", "a"],
            "census_year": [1890, 1890],
            "population": [10.0, 10.0],
            "irish_born_count": [2.0, 2.0],
            "irish_born_share": [0.2, 0.2],
        }
    )
    report = validate_panel(panel)
    assert not report["valid"]
    assert "duplicate county_1940_id + census_year keys" in report["errors"]
    assert "1890 must not be manufactured" in report["errors"]


def test_validation_accepts_a_minimal_valid_panel():
    panel = pd.DataFrame(
        {
            "county_1940_id": ["a", "a"],
            "census_year": [1880, 1900],
            "population": [10.0, 12.0],
            "irish_born_count": [2.0, 3.0],
            "irish_born_share": [0.2, 0.25],
        }
    )
    report = validate_panel(panel)
    assert report["valid"]
