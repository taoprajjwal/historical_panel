"""Build a fixed-1940-boundary U.S. county panel for 1850--1940.

Counts are allocated from each census year's county geography to 1940 county
boundaries by polygon-area overlap.  This is transparent and reproducible, but
it assumes people and economic activity are uniformly distributed inside each
source county.  The output therefore retains overlap diagnostics and provenance
flags rather than treating harmonization as exact.
"""

from __future__ import annotations

import argparse
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd


CENSUS_YEARS = (1850, 1860, 1870, 1880, 1900, 1910, 1920, 1930, 1940)
LAYER_BY_YEAR = {
    1850: 7,
    1860: 8,
    1870: 9,
    1880: 10,
    1900: 12,
    1910: 13,
    1920: 14,
    1930: 15,
    1940: 16,
    2010: 23,
}
ARCGIS_ROOT = (
    "https://services4.arcgis.com/QdHwhlbx61LR3TWb/ArcGIS/rest/services/"
    "US_States_and_Counties_WFL1/FeatureServer"
)
CONTERMINOUS_EXCLUSIONS = {"Alaska", "Hawaii", "Puerto Rico"}
CONTERMINOUS_STATE_CODE_EXCLUSIONS = {"02", "15", "72"}
RAIL_SNAPSHOT_YEARS = (1873, 1883, 1898, 1912, 1921)
RAIL_SNAPSHOT_FOR_CENSUS = {
    1880: 1873,
    1900: 1898,
    1910: 1898,
    1920: 1912,
    1930: 1921,
    1940: 1921,
}

COUNT_COLUMNS = (
    "population",
    "urban_population",
    "foreign_born_count",
    "irish_born_count",
    "male_population",
    "female_population",
    "working_age_count",
    "illiterate_count",
    "literacy_denominator",
    "manufacturing_workers",
    "manufacturing_wages_nominal",
    "farm_value_nominal",
    "farm_acres",
    "wealth_nominal",
    "labor_force_count",
    "employed_count",
    "agricultural_workers",
    "professional_workers",
    "clerical_sales_workers",
    "craft_operatives_workers",
    "service_workers",
    "radio_households",
    "occupied_households",
)


def _coalesce(frame: pd.DataFrame, names: Iterable[str]) -> pd.Series:
    result = pd.Series(np.nan, index=frame.index, dtype="float64")
    for name in names:
        if name in frame:
            result = result.fillna(pd.to_numeric(frame[name], errors="coerce"))
    return result


def _sum_if_available(frame: pd.DataFrame, names: Iterable[str]) -> pd.Series:
    pieces = [pd.to_numeric(frame[name], errors="coerce") for name in names if name in frame]
    if not pieces:
        return pd.Series(np.nan, index=frame.index, dtype="float64")
    values = pd.concat(pieces, axis=1)
    return values.sum(axis=1, min_count=1)


def standardize_source(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert changing historical Census names into conservative common fields."""
    out = pd.DataFrame(index=frame.index)
    out["census_year"] = pd.to_numeric(frame["year"], errors="coerce").astype("Int64")
    out["source_gisjoin"] = frame["gisjoin"].astype("string").str.strip()
    out["source_county_name"] = frame["countyname_haines"].astype("string")
    out["source_state_name"] = frame["STATENAM"].astype("string")
    # Haines uses decimal suffixes for a small number of historical county
    # combinations, so this must not be coerced to an integer modern FIPS.
    out["source_fips_haines"] = pd.to_numeric(frame["fips"], errors="coerce")
    out["population"] = _coalesce(frame, ("totpop", "totpop40", "totpop30"))

    year = out["census_year"]
    urban_sources = {
        1850: ("urb850",),
        1860: ("urb860",),
        1870: ("urb870",),
        1880: (),
        1900: (),
        1910: ("urb910",),
        1920: ("urb920", "urban20"),
        1930: ("urb930", "urban30"),
        1940: ("urb940", "urban40"),
    }
    out["urban_population"] = np.nan
    for value, names in urban_sources.items():
        out.loc[year.eq(value), "urban_population"] = _coalesce(frame, names)[year.eq(value)]

    out["irish_born_count"] = np.nan
    out.loc[year.isin((1870, 1880, 1900)), "irish_born_count"] = pd.to_numeric(
        frame["pbirelan"], errors="coerce"
    )[year.isin((1870, 1880, 1900))]
    out.loc[year.isin((1910, 1920)), "irish_born_count"] = pd.to_numeric(
        frame["pbwirel"], errors="coerce"
    )[year.isin((1910, 1920))]
    modern_irish = _sum_if_available(frame, ("pbweire", "pbwulst"))
    out.loc[year.isin((1930, 1940)), "irish_born_count"] = modern_irish[
        year.isin((1930, 1940))
    ]

    out["foreign_born_count"] = np.nan
    foreign_sources = {
        1850: ("foreign",),
        1860: ("fbfree",),
        1870: ("fbtot",),
        1880: ("fbtot",),
        1900: ("fbmtot", "fbftot"),
        1910: ("fbwtot",),
        1940: ("fbmtot", "fbftot"),
    }
    for value, names in foreign_sources.items():
        mask = year.eq(value)
        out.loc[mask, "foreign_born_count"] = _sum_if_available(frame, names)[mask]

    out["male_population"] = _coalesce(frame, ("mtot",))
    out["female_population"] = _coalesce(frame, ("ftot",))
    working_age_parts = (
        "m1519", "m2029", "m3039", "m4049", "m5059",
        "f1519", "f2029", "f3039", "f4049", "f5059",
    )
    out["working_age_count"] = _sum_if_available(frame, working_age_parts)

    out["illiterate_count"] = np.nan
    out["literacy_denominator"] = np.nan
    mask = year.eq(1850)
    out.loc[mask, "illiterate_count"] = pd.to_numeric(frame["totillit"], errors="coerce")[mask]
    out.loc[mask, "literacy_denominator"] = out.loc[mask, "population"]
    mask = year.isin((1910, 1920, 1930))
    out.loc[mask, "illiterate_count"] = pd.to_numeric(frame["tillit10"], errors="coerce")[mask]
    # The published table does not provide a stable 10+ denominator in every year.
    # Population is retained as an explicitly named proxy denominator.
    out.loc[mask, "literacy_denominator"] = out.loc[mask, "population"]

    out["manufacturing_workers"] = np.nan
    manufacturing_sources = {
        1850: ("mfglabor",),
        1860: ("mfglabm", "mfglabf"),
        1870: ("mfglabor",),
        1880: ("mfglbm16", "mfglbf16"),
        1900: ("mfgavear",),
        1920: ("mfgavear",),
        1930: ("mfgavear",),
        1940: ("mfgavear",),
    }
    for value, names in manufacturing_sources.items():
        mask = year.eq(value)
        out.loc[mask, "manufacturing_workers"] = _sum_if_available(frame, names)[mask]

    out["manufacturing_wages_nominal"] = np.nan
    wage_sources = {
        1860: ("mfglcost",),
        1870: ("mfgwages",),
        1880: ("mfgwages",),
        1900: ("mfgwages",),
        1920: ("mfgwages",),
        1930: ("mfgwages",),
        1940: ("mfgwages",),
    }
    for value, names in wage_sources.items():
        mask = year.eq(value)
        out.loc[mask, "manufacturing_wages_nominal"] = _sum_if_available(frame, names)[mask]

    out["farm_value_nominal"] = pd.to_numeric(frame["farmval"], errors="coerce")
    out["farm_acres"] = _coalesce(frame, ("acres",))
    out["wealth_nominal"] = _coalesce(frame, ("wealth", "wealthas"))
    out["labor_force_count"] = np.where(
        year.eq(1940), _sum_if_available(frame, ("m14lf", "f14lf")), np.nan
    )
    out["employed_count"] = np.where(
        year.eq(1940), _sum_if_available(frame, ("m14emp", "f14emp")), np.nan
    )
    out["agricultural_workers"] = np.where(
        year.eq(1940),
        _sum_if_available(
            frame, ("mfarm", "ffarm", "mflabwag", "fflabwag", "mflabufw", "fflabufw")
        ),
        np.nan,
    )
    out["professional_workers"] = np.where(
        year.eq(1940), _sum_if_available(frame, ("mprof", "fprof", "msprof", "fsprof")), np.nan
    )
    out["clerical_sales_workers"] = np.where(
        year.eq(1940), _sum_if_available(frame, ("mclerk", "fclerk")), np.nan
    )
    out["craft_operatives_workers"] = np.where(
        year.eq(1940), _sum_if_available(frame, ("mcraft", "fcraft", "moperat", "foperat")), np.nan
    )
    out["service_workers"] = np.where(
        year.eq(1940),
        _sum_if_available(frame, ("mdomest", "fdomest", "mservoth", "fservoth")),
        np.nan,
    )
    out["radio_households"] = np.where(
        year.eq(1940), pd.to_numeric(frame["dwradio"], errors="coerce"), np.nan
    )
    out["occupied_households"] = np.where(
        year.eq(1940), pd.to_numeric(frame["dwelloc2"], errors="coerce"), np.nan
    )
    out["rail_access_source"] = pd.to_numeric(frame["rail"], errors="coerce")
    out["water_access_source"] = pd.to_numeric(frame["water"], errors="coerce")
    # A few deposited historical rows contain a component count larger than
    # total population (usually a boundary/key anomaly).  Missing is safer than
    # allowing an impossible share into the harmonized panel.
    for column in ("urban_population", "manufacturing_workers"):
        flag = out[column].gt(out["population"]) & out["population"].notna()
        out[f"{column}_invalid_source"] = flag
        out.loc[flag, column] = np.nan
    out["source_observation"] = "observed_county_aggregate"
    valid_gisjoin = out["source_gisjoin"].notna() & out["source_gisjoin"].str.startswith("G")
    result = out[out["census_year"].isin(CENSUS_YEARS) & valid_gisjoin].copy()
    duplicate = result.duplicated(["census_year", "source_gisjoin"], keep=False)
    if duplicate.any():
        examples = result.loc[duplicate, ["census_year", "source_gisjoin"]].head().to_dict("records")
        raise RuntimeError(f"Duplicate source county-year keys after source cleanup: {examples}")
    return result


def clean_source_keys(frame: pd.DataFrame) -> pd.DataFrame:
    """Resolve a handful of duplicate/blank GISJOIN records in the deposit.

    The source contains empty territorial placeholders and six duplicated
    county-year GISJOINs.  We prefer a county row with data and, where possible,
    the Haines FIPS that agrees with the NHGIS GISJOIN.  The discarded rows are
    source-key anomalies, not observations that can be safely summed.
    """
    gisjoin = frame["gisjoin"].astype("string").str.strip()
    frame = frame[gisjoin.str.startswith("G", na=False)].copy()
    frame["gisjoin"] = gisjoin[gisjoin.str.startswith("G", na=False)]
    expected_fips = pd.to_numeric(
        frame["gisjoin"].str.slice(1, 3) + frame["gisjoin"].str.slice(4, 7),
        errors="coerce",
    )
    actual_fips = pd.to_numeric(frame["fips"], errors="coerce")
    frame["_fips_agrees"] = np.isclose(actual_fips, expected_fips, equal_nan=False)
    frame["_has_population"] = pd.to_numeric(frame["totpop"], errors="coerce").notna()
    frame = frame.sort_values(
        ["year", "gisjoin", "_fips_agrees", "_has_population"],
        ascending=[True, True, False, False],
    )
    frame = frame.drop_duplicates(["year", "gisjoin"], keep="first")
    return frame.drop(columns=["_fips_agrees", "_has_population"])


def read_source_panel(path: Path) -> pd.DataFrame:
    required = {
        "year", "gisjoin", "countyname_haines", "STATENAM", "fips", "totpop",
        "totpop40", "totpop30", "urb850", "urb860", "urb870", "urb910",
        "urb920", "urb930", "urb940", "urban20", "urban30", "urban40",
        "pbirelan", "pbwirel", "pbweire", "pbwulst", "foreign", "fbfree",
        "fbtot", "fbmtot", "fbftot", "fbwtot", "mtot", "ftot", "m1519",
        "m2029", "m3039", "m4049", "m5059", "f1519", "f2029", "f3039",
        "f4049", "f5059", "totillit", "tillit10", "mfglabor", "mfglabm",
        "mfglabf", "mfglbm16", "mfglbf16", "mfgavear", "mfgwages",
        "mfglcost", "farmval", "acres", "wealth", "wealthas", "m14lf", "f14lf",
        "m14emp", "f14emp", "mfarm", "ffarm", "mflabwag", "fflabwag",
        "mflabufw", "fflabufw", "mprof", "fprof", "msprof", "fsprof",
        "mclerk", "fclerk", "mcraft", "fcraft", "moperat", "foperat",
        "mdomest", "fdomest", "mservoth", "fservoth", "dwradio", "dwelloc2",
        "rail", "water", "level",
    }
    labels = pd.io.stata.StataReader(path).variable_labels()
    columns = sorted(required.intersection(labels))
    missing = sorted({"year", "gisjoin", "totpop"} - set(columns))
    if missing:
        raise RuntimeError(f"County source is missing required columns: {missing}")
    frame = pd.read_stata(path, columns=columns, convert_categoricals=False)
    return standardize_source(clean_source_keys(frame))


def _download_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "historical-panel/1.0"})
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def county_geometry(year: int, cache_dir: Path) -> gpd.GeoDataFrame:
    """Download one decennial county layer from the public ArcGIS service."""
    if year not in LAYER_BY_YEAR:
        raise ValueError(f"No configured geometry layer for {year}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"counties_{year}.parquet"
    if cached.exists():
        return gpd.read_parquet(cached)
    layer = LAYER_BY_YEAR[year]
    count_url = f"{ARCGIS_ROOT}/{layer}/query?" + urllib.parse.urlencode(
        {"where": "1=1", "returnCountOnly": "true", "f": "json"}
    )
    count = int(_download_json(count_url)["count"])
    features: list[dict] = []
    for offset in range(0, count, 2000):
        if year == 2010:
            out_fields = "GISJOIN,STATEFP10,COUNTYFP10,NAME10"
        else:
            out_fields = "GISJOIN,DECADE,NHGISNAM,STATENAM,STATE,COUNTY,ICPSRFIP"
        params = {
            "where": "1=1",
            "outFields": out_fields,
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
            "resultOffset": str(offset),
            "resultRecordCount": "2000",
        }
        url = f"{ARCGIS_ROOT}/{layer}/query?" + urllib.parse.urlencode(params)
        payload = _download_json(url)
        if "features" not in payload:
            raise RuntimeError(f"ArcGIS query failed for {year} at offset {offset}: {payload}")
        features.extend(payload["features"])
    frame = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    if year == 2010:
        frame = frame.rename(
            columns={"STATEFP10": "STATE", "COUNTYFP10": "COUNTY", "NAME10": "NHGISNAM"}
        )
        frame["STATENAM"] = pd.NA
        frame = frame[~frame["STATE"].isin(CONTERMINOUS_STATE_CODE_EXCLUSIONS)].copy()
    else:
        frame = frame[~frame["STATENAM"].isin(CONTERMINOUS_EXCLUSIONS)].copy()
    frame["GISJOIN"] = frame["GISJOIN"].astype("string")
    frame.to_parquet(cached, index=False)
    return frame


def build_crosswalk(
    source: gpd.GeoDataFrame, target: gpd.GeoDataFrame, year: int
) -> pd.DataFrame:
    """Intersect source-year counties with 1940 counties and retain diagnostics."""
    source = source.to_crs("EPSG:5070").copy()
    target = target.to_crs("EPSG:5070").copy()
    source["source_area_sq_m"] = source.geometry.area
    target["target_area_sq_m"] = target.geometry.area
    left = source[["GISJOIN", "source_area_sq_m", "geometry"]].rename(
        columns={"GISJOIN": "source_gisjoin"}
    )
    right = target[
        ["GISJOIN", "STATE", "COUNTY", "NHGISNAM", "STATENAM", "target_area_sq_m", "geometry"]
    ].rename(
        columns={
            "GISJOIN": "county_1940_id",
            "STATE": "state_code_1940",
            "COUNTY": "county_code_1940",
            "NHGISNAM": "county_name_1940",
            "STATENAM": "state_name_1940",
        }
    )
    intersections = gpd.overlay(left, right, how="intersection", keep_geom_type=False)
    intersections["intersection_area_sq_m"] = intersections.geometry.area
    intersections["source_area_weight"] = (
        intersections["intersection_area_sq_m"] / intersections["source_area_sq_m"]
    )
    intersections["target_area_weight"] = (
        intersections["intersection_area_sq_m"] / intersections["target_area_sq_m"]
    )
    intersections["source_year"] = year
    weight_sum = intersections.groupby("source_gisjoin")["source_area_weight"].transform("sum")
    intersections["source_weight_sum"] = weight_sum
    intersections["crosswalk_quality"] = np.select(
        [weight_sum.between(0.98, 1.02), weight_sum.between(0.90, 1.10)],
        ["good", "review"],
        default="poor",
    )
    intersections["crosswalk_method"] = "area_overlap_equal_distribution"
    columns = [
        "source_year", "source_gisjoin", "county_1940_id", "state_code_1940",
        "county_code_1940", "county_name_1940", "state_name_1940",
        "intersection_area_sq_m", "source_area_sq_m", "target_area_sq_m",
        "source_area_weight", "target_area_weight", "source_weight_sum",
        "crosswalk_quality", "crosswalk_method",
    ]
    return pd.DataFrame(intersections[columns])


def allocate_year(source: pd.DataFrame, crosswalk: pd.DataFrame) -> pd.DataFrame:
    merged = crosswalk.merge(source, on="source_gisjoin", how="left", validate="many_to_one")
    for column in COUNT_COLUMNS:
        merged[f"allocated_{column}"] = merged[column] * merged["source_area_weight"]
    for column in ("rail_access_source", "water_access_source"):
        merged[f"weighted_{column}"] = merged[column] * merged["target_area_weight"]
    grouped = merged.groupby(
        [
            "county_1940_id", "state_code_1940", "county_code_1940",
            "county_name_1940", "state_name_1940", "target_area_sq_m",
        ],
        dropna=False,
        observed=True,
    )
    allocated = grouped[[f"allocated_{column}" for column in COUNT_COLUMNS]].sum(min_count=1)
    access = grouped[["weighted_rail_access_source", "weighted_water_access_source"]].sum(
        min_count=1
    )
    out = pd.concat([allocated, access], axis=1).reset_index()
    out = out.rename(columns={f"allocated_{column}": column for column in COUNT_COLUMNS})
    out = out.rename(
        columns={
            "weighted_rail_access_source": "rail_access_area_share",
            "weighted_water_access_source": "water_access_area_share",
        }
    )
    out["census_year"] = int(source["census_year"].iloc[0])
    out["land_area_sq_miles_1940"] = out["target_area_sq_m"] / 2_589_988.110336
    out["population_density"] = out["population"] / out["land_area_sq_miles_1940"]
    out["urban_share"] = out["urban_population"] / out["population"]
    out["foreign_born_share"] = out["foreign_born_count"] / out["population"]
    out["irish_born_share"] = out["irish_born_count"] / out["population"]
    out["sex_ratio_male_per_female"] = out["male_population"] / out["female_population"]
    out["working_age_share"] = out["working_age_count"] / out["population"]
    out["illiteracy_population_proxy_share"] = out["illiterate_count"] / out["literacy_denominator"]
    out["manufacturing_employment_population_share"] = (
        out["manufacturing_workers"] / out["population"]
    )
    out["manufacturing_wages_per_worker_nominal"] = (
        out["manufacturing_wages_nominal"] / out["manufacturing_workers"]
    )
    out["farm_value_per_person_nominal"] = out["farm_value_nominal"] / out["population"]
    out["farm_value_per_acre_nominal"] = out["farm_value_nominal"] / out["farm_acres"]
    out["wealth_per_person_nominal"] = out["wealth_nominal"] / out["population"]
    out["labor_force_population_proxy_share"] = out["labor_force_count"] / out["population"]
    out["employment_population_proxy_share"] = out["employed_count"] / out["population"]
    out["agricultural_employment_share"] = out["agricultural_workers"] / out["employed_count"]
    out["professional_employment_share"] = out["professional_workers"] / out["employed_count"]
    out["clerical_sales_employment_share"] = out["clerical_sales_workers"] / out["employed_count"]
    out["craft_operatives_employment_share"] = (
        out["craft_operatives_workers"] / out["employed_count"]
    )
    out["service_employment_share"] = out["service_workers"] / out["employed_count"]
    out["radio_household_share"] = out["radio_households"] / out["occupied_households"]
    out["geography_vintage"] = 1940
    out["value_status"] = "area_harmonized_from_observed_aggregate"
    return out


def add_market_potential(panel: pd.DataFrame, targets: gpd.GeoDataFrame) -> pd.DataFrame:
    centroids = targets.to_crs("EPSG:5070").copy()
    centroids["centroid"] = centroids.geometry.centroid
    coordinates = np.column_stack((centroids["centroid"].x, centroids["centroid"].y)) / 1000.0
    ids = centroids["GISJOIN"].astype(str).to_numpy()
    id_to_position = {county_id: position for position, county_id in enumerate(ids)}
    distances = np.sqrt(
        ((coordinates[:, None, :] - coordinates[None, :, :]) ** 2).sum(axis=2)
    )
    np.fill_diagonal(distances, np.inf)
    result = panel.copy()
    result["market_potential_persons_per_km"] = np.nan
    for year, indexes in result.groupby("census_year").groups.items():
        locations = result.loc[indexes, "county_1940_id"].map(id_to_position).to_numpy()
        population = np.zeros(len(ids), dtype=float)
        values = result.loc[indexes, "population"].to_numpy(dtype=float)
        valid = ~pd.isna(locations)
        population[locations[valid].astype(int)] = np.nan_to_num(values[valid], nan=0.0)
        potential = (population[None, :] / distances).sum(axis=1)
        result.loc[indexes, "market_potential_persons_per_km"] = potential[
            locations.astype(int)
        ]
    return result


def add_rail_snapshots(
    panel: pd.DataFrame, targets: gpd.GeoDataFrame, raw_root: Path
) -> pd.DataFrame:
    """Add conservative rail access from the latest snapshot before a census.

    A county is connected when a mapped rail line intersects its fixed 1940
    polygon.  Snapshot years are not interpreted as exact construction dates.
    There is no pre-1870 snapshot in this deposit, so 1870 stays missing.
    """
    target = targets.to_crs("ESRI:102003")[["GISJOIN", "geometry"]].copy()
    target = target.rename(columns={"GISJOIN": "county_1940_id"})
    observations: list[pd.DataFrame] = []
    rail_root = raw_root / "Data - raw/Sequeira et al IV"
    for snapshot_year in RAIL_SNAPSHOT_YEARS:
        path = rail_root / f"railroads{snapshot_year}" / f"railroads{snapshot_year}.shp"
        if not path.exists():
            raise FileNotFoundError(
                f"Missing rail snapshot {path}; rerun historical_panel.download_sources"
            )
        rail = gpd.read_file(path, columns=[], engine="pyogrio").to_crs(target.crs)
        joined = gpd.sjoin(target, rail[["geometry"]], how="left", predicate="intersects")
        connected = joined.groupby("county_1940_id")["index_right"].apply(
            lambda values: int(values.notna().any())
        )
        observations.append(
            connected.rename("rail_connected").reset_index().assign(
                rail_snapshot_year=snapshot_year
            )
        )
    rail_long = pd.concat(observations, ignore_index=True)
    first = (
        rail_long[rail_long["rail_connected"].eq(1)]
        .groupby("county_1940_id")["rail_snapshot_year"]
        .min()
        .rename("first_observed_rail_snapshot_year")
    )
    result = panel.copy()
    result["rail_snapshot_year"] = result["census_year"].map(RAIL_SNAPSHOT_FOR_CENSUS).astype("Int64")
    result = result.merge(
        rail_long,
        on=["county_1940_id", "rail_snapshot_year"],
        how="left",
        validate="many_to_one",
    )
    result = result.merge(first, on="county_1940_id", how="left", validate="many_to_one")
    result["first_observed_rail_snapshot_year"] = result[
        "first_observed_rail_snapshot_year"
    ].astype("Int64")
    result["rail_access_definition"] = np.where(
        result["rail_snapshot_year"].notna(),
        "line_intersects_1940_county_latest_prior_snapshot",
        pd.NA,
    )
    return result


def build_1940_to_2010_crosswalk(
    target_1940: gpd.GeoDataFrame, target_2010: gpd.GeoDataFrame
) -> pd.DataFrame:
    left = target_1940.to_crs("EPSG:5070").copy()
    right = target_2010.to_crs("EPSG:5070").copy()
    state_names = (
        target_1940[["STATE", "STATENAM"]]
        .dropna()
        .drop_duplicates("STATE")
        .set_index("STATE")["STATENAM"]
    )
    right["STATENAM"] = right["STATENAM"].fillna(right["STATE"].map(state_names))
    left["area_1940_sq_m"] = left.geometry.area
    right["area_2010_sq_m"] = right.geometry.area
    left = left[["GISJOIN", "area_1940_sq_m", "geometry"]].rename(
        columns={"GISJOIN": "county_1940_id"}
    )
    right = right[["GISJOIN", "NHGISNAM", "STATENAM", "area_2010_sq_m", "geometry"]].rename(
        columns={
            "GISJOIN": "county_2010_id",
            "NHGISNAM": "county_name_2010",
            "STATENAM": "state_name_2010",
        }
    )
    overlay = gpd.overlay(left, right, how="intersection", keep_geom_type=False)
    overlay["intersection_area_sq_m"] = overlay.geometry.area
    overlay["share_of_1940_area"] = overlay["intersection_area_sq_m"] / overlay["area_1940_sq_m"]
    overlay["share_of_2010_area"] = overlay["intersection_area_sq_m"] / overlay["area_2010_sq_m"]
    return pd.DataFrame(
        overlay[
            [
                "county_1940_id", "county_2010_id", "county_name_2010",
                "state_name_2010", "intersection_area_sq_m", "share_of_1940_area",
                "share_of_2010_area",
            ]
        ]
    )


def validate_panel(
    panel: pd.DataFrame,
    source: pd.DataFrame | None = None,
    crosswalk: pd.DataFrame | None = None,
) -> dict:
    errors: list[str] = []
    if panel.duplicated(["county_1940_id", "census_year"]).any():
        errors.append("duplicate county_1940_id + census_year keys")
    years = set(pd.to_numeric(panel["census_year"], errors="coerce").dropna().astype(int))
    unexpected = sorted(years - set(CENSUS_YEARS))
    if unexpected:
        errors.append(f"unexpected census years: {unexpected}")
    if 1890 in years:
        errors.append("1890 must not be manufactured")
    for column in COUNT_COLUMNS:
        if column in panel and panel[column].dropna().lt(-1e-8).any():
            errors.append(f"negative values in {column}")
    share_columns = [column for column in panel if column.endswith("_share")]
    for column in share_columns:
        values = panel[column].dropna()
        if ((values < -1e-8) | (values > 1.000001)).any():
            errors.append(f"out-of-range share in {column}")
    if (panel["irish_born_count"] > panel["population"] + 1e-6).fillna(False).any():
        errors.append("Irish-born count exceeds population")
    report = {
        "valid": not errors,
        "errors": errors,
        "rows": int(len(panel)),
        "counties": int(panel["county_1940_id"].nunique()),
        "years": sorted(years),
        "missingness_by_year": {
            str(int(year)): {
                column: float(value)
                for column, value in group.isna().mean().items()
                if column in COUNT_COLUMNS or column.endswith("_share")
            }
            for year, group in panel.groupby("census_year")
        },
    }
    if source is not None and crosswalk is not None:
        reconciliation: dict[str, dict[str, float | int]] = {}
        for year in CENSUS_YEARS:
            year_source = source[source["census_year"].eq(year)]
            matched = set(
                crosswalk.loc[crosswalk["source_year"].eq(year), "source_gisjoin"]
            )
            source_total = year_source.loc[
                year_source["source_gisjoin"].isin(matched), "population"
            ].sum(min_count=1)
            panel_total = panel.loc[panel["census_year"].eq(year), "population"].sum(
                min_count=1
            )
            reconciliation[str(year)] = {
                "matched_source_population": float(source_total),
                "harmonized_panel_population": float(panel_total),
                "relative_difference": float((panel_total - source_total) / source_total),
                "source_counties_matched": int(
                    year_source["source_gisjoin"].isin(matched).sum()
                ),
            }
        report["population_reconciliation"] = reconciliation
        source_weights = crosswalk.drop_duplicates(["source_year", "source_gisjoin"])[
            ["source_year", "source_weight_sum", "crosswalk_quality"]
        ]
        report["crosswalk_quality_counts"] = {
            str(year): {
                str(key): int(value)
                for key, value in group["crosswalk_quality"].value_counts().items()
            }
            for year, group in source_weights.groupby("source_year")
        }
    return report


def coverage_table(panel: pd.DataFrame) -> pd.DataFrame:
    identifiers = {
        "county_1940_id", "state_code_1940", "county_code_1940",
        "county_name_1940", "state_name_1940", "census_year",
    }
    rows: list[dict] = []
    for year, group in panel.groupby("census_year"):
        for variable in (column for column in panel.columns if column not in identifiers):
            rows.append(
                {
                    "census_year": int(year),
                    "variable": variable,
                    "rows": int(len(group)),
                    "nonmissing": int(group[variable].notna().sum()),
                    "coverage_share": float(group[variable].notna().mean()),
                }
            )
    return pd.DataFrame(rows)


def county_history_examples(panel: pd.DataFrame) -> pd.DataFrame:
    requested = {
        ("Suffolk", "Massachusetts"),
        ("New York", "New York"),
        ("Cook", "Illinois"),
    }
    mask = pd.Series(False, index=panel.index)
    for county, state in requested:
        mask |= panel["county_name_1940"].eq(county) & panel["state_name_1940"].eq(state)
    columns = [
        "county_1940_id", "county_name_1940", "state_name_1940", "census_year",
        "population", "irish_born_count", "irish_born_share", "urban_share",
        "population_density", "rail_connected", "rail_snapshot_year",
        "market_potential_persons_per_km", "manufacturing_wages_per_worker_nominal",
    ]
    return panel.loc[mask, columns].sort_values(
        ["state_name_1940", "county_name_1940", "census_year"]
    )


def source_quality_flags(source: pd.DataFrame) -> pd.DataFrame:
    records: list[pd.DataFrame] = []
    for variable in ("urban_population", "manufacturing_workers"):
        flag = f"{variable}_invalid_source"
        if flag not in source:
            continue
        affected = source[source[flag]].copy()
        if affected.empty:
            continue
        affected["variable"] = variable
        affected["issue"] = "source component count exceeded source total population; set missing"
        records.append(
            affected[
                [
                    "census_year", "source_gisjoin", "source_county_name",
                    "source_state_name", "population", "variable", "issue",
                ]
            ]
        )
    if not records:
        return pd.DataFrame(
            columns=[
                "census_year", "source_gisjoin", "source_county_name",
                "source_state_name", "population", "variable", "issue",
            ]
        )
    return pd.concat(records, ignore_index=True)


def country_music_example(path: Path) -> pd.DataFrame:
    frame = pd.read_stata(path, convert_categoricals=False)
    out = pd.DataFrame()
    out["geography_type"] = "county_2010_fips"
    out["geography_id"] = pd.to_numeric(frame["county_fips"], errors="coerce").astype("Int64")
    out["observation_year"] = 2021
    out["music_measure"] = "country_music_radio_station_count"
    out["value"] = pd.to_numeric(frame["stations"], errors="coerce")
    out["unit"] = "stations"
    out["genre_definition"] = "country format as classified by source radio companies"
    out["source_id"] = "bffpt_qje2023_country_radio"
    out["coverage_note"] = "iHeartRadio, Cumulus, and Audacy stations; not all U.S. radio"
    out["is_irish_music_outcome"] = False
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--raw-root",
        type=Path,
        default=Path("historical_panel/data/raw/BFFPT-QJE2023-Replication"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("historical_panel/data/processed"))
    parser.add_argument("--geometry-cache", type=Path, default=Path("historical_panel/data/geometry"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw_root = args.raw_root.resolve()
    output_dir = args.output_dir.resolve()
    geometry_cache = args.geometry_cache.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_path = raw_root / "Data - raw/Census/County/1850_1940_census_county.dta"
    source = read_source_panel(source_path)
    target_1940 = county_geometry(1940, geometry_cache)

    crosswalk_parts: list[pd.DataFrame] = []
    panel_parts: list[pd.DataFrame] = []
    for year in CENSUS_YEARS:
        source_year = source[source["census_year"].eq(year)].copy()
        geometry = county_geometry(year, geometry_cache)
        crosswalk = build_crosswalk(geometry, target_1940, year)
        crosswalk_parts.append(crosswalk)
        panel_parts.append(allocate_year(source_year, crosswalk))
    crosswalk = pd.concat(crosswalk_parts, ignore_index=True)
    panel = pd.concat(panel_parts, ignore_index=True)
    panel = add_market_potential(panel, target_1940)
    panel = add_rail_snapshots(panel, target_1940, raw_root)
    panel = panel.sort_values(["county_1940_id", "census_year"]).reset_index(drop=True)

    target_2010 = county_geometry(2010, geometry_cache)
    crosswalk_2010 = build_1940_to_2010_crosswalk(target_1940, target_2010)
    report = validate_panel(panel, source=source, crosswalk=crosswalk)
    if not report["valid"]:
        raise RuntimeError("Panel validation failed: " + "; ".join(report["errors"]))

    panel.to_parquet(output_dir / "county_decade_panel.parquet", index=False)
    panel.to_csv(output_dir / "county_decade_panel.csv.gz", index=False)
    crosswalk.to_parquet(output_dir / "county_crosswalk.parquet", index=False)
    crosswalk_2010.to_parquet(output_dir / "county_1940_to_2010_crosswalk.parquet", index=False)
    music_path = raw_root / "Data - raw/Country Music Radio Stations/CountryStationsCounts.dta"
    country_music_example(music_path).to_csv(
        output_dir / "country_music_radio_example_2021.csv", index=False
    )
    coverage_table(panel).to_csv(output_dir / "variable_coverage.csv", index=False)
    county_history_examples(panel).to_csv(
        output_dir / "county_history_examples.csv", index=False
    )
    source_quality_flags(source).to_csv(output_dir / "source_quality_flags.csv", index=False)
    with (output_dir / "validation_report.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    print(f"panel_rows={len(panel):,}")
    print(f"panel_counties={panel['county_1940_id'].nunique():,}")
    print(f"crosswalk_rows={len(crosswalk):,}")
    print(f"output_dir={output_dir}")


if __name__ == "__main__":
    main()
