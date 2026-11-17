"""Stream IPUMS full counts into county-decade aggregates for the panel.

The source ``.dat.gz`` is never expanded to disk.  Person and household
records are reduced to additive numerators and denominators on the county
geography of each census year.  A second stage attaches NHGIS identifiers and
uses the existing historical-to-1940 crosswalk to create a join-ready panel
extension.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from ipums_convert import (
    parse_fixed_width_block,
    read_ddi,
    sha256_file,
    validate_ddi_for_data,
)

from historical_panel.build_panel import ARCGIS_ROOT, CENSUS_YEARS, LAYER_BY_YEAR


FULL_COUNT_SAMPLES = {
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
FULL_COUNT_SAMPLE_IDS = set(FULL_COUNT_SAMPLES.values())
KEY_COLUMNS = (
    "census_year",
    "sample",
    "stateicp",
    "statefip",
    "countyicp",
    "countynhg",
)
PARSE_FIELDS = (
    "YEAR", "SAMPLE", "SERIAL", "HHWT", "REGION", "STATEICP", "STATEFIP",
    "COUNTYICP", "COUNTYNHG", "METRO", "METAREA", "CITY", "CITYPOP",
    "SIZEPL", "URBAN", "URBPOP", "SEA", "GQ", "GQTYPE", "FARM",
    "OWNERSHP", "MORTGAGE", "RENT", "RENT30", "VALUEH", "RADIO30",
    "MULTGEN", "PERNUM", "PERWT", "SLWT", "FAMUNIT", "FAMSIZE", "MOMLOC",
    "POPLOC", "SPLOC", "NCHILD", "NCHLT5", "RELATE", "SEX", "AGE",
    "MARST", "BIRTHYR", "RACE", "BPL", "BPLD", "MBPL", "MBPLD", "FBPL",
    "FBPLD", "NATIVITY", "CITIZEN", "YRIMMIG", "YRSUSA1", "YRSUSA2",
    "MTONGUE", "MTONGUED", "LANGUAGE", "LANGUAGED", "SPEAKENG", "SCHOOL",
    "SCHLMNTH", "HIGRADE", "EDUC", "LIT", "EMPSTAT", "LABFORCE",
    "CLASSWKR", "OCC", "OCC1950", "IND", "IND1950", "WKSWORK1",
    "HRSWORK1", "MOUNEMP", "WKSUNEMP", "DURUNEMP", "INCWAGE", "INCNONWG",
    "REALPROP", "PERSPROP", "OCCSCORE", "SEI", "PRESGL", "ERSCOR50",
    "EDSCOR50", "NPBOSS50", "MIGRATE5", "MIGPLAC5", "MIGCOUNTYICP5",
    "MIGMETAREA5", "MIGCITY5", "MIGSEA5", "SAMEPLAC5", "SAMESEA5",
)
IRISH_BPL = 414
IRISH_LANGUAGE_STRICT = {1540, 1560}
IRISH_LANGUAGE_BROAD = {1540, 1550, 1560}
HOUSEHOLD_GQ_CODES = {1, 2, 5}


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _numeric(frame: pd.DataFrame, name: str) -> pd.Series:
    return pd.to_numeric(frame[name], errors="coerce")


def _parse_and_aggregate(
    block: bytes,
    ddi,
) -> tuple[pd.DataFrame, int, int]:
    """Parse one independent fixed-width block in a worker process."""

    parsed = parse_fixed_width_block(block, ddi=ddi, fields=PARSE_FIELDS)
    selected_rows = int(parsed["sample"].isin(FULL_COUNT_SAMPLE_IDS).sum())
    return aggregate_chunk(parsed), len(parsed), selected_rows


def aggregate_chunk(frame: pd.DataFrame) -> pd.DataFrame:
    """Return additive county totals for one parsed person-record chunk."""

    frame = frame[frame["sample"].isin(FULL_COUNT_SAMPLE_IDS)].copy()
    frame = frame[
        frame["year"].isin(CENSUS_YEARS)
        & _numeric(frame, "stateicp").gt(0)
        & _numeric(frame, "countyicp").gt(0)
        & ~_numeric(frame, "statefip").isin((2, 15, 72))
    ].copy()
    if frame.empty:
        return pd.DataFrame(columns=KEY_COLUMNS)

    keys = pd.DataFrame(
        {
            "census_year": _numeric(frame, "year").astype("int16"),
            "sample": _numeric(frame, "sample").astype("int32"),
            "stateicp": _numeric(frame, "stateicp").astype("int16"),
            "statefip": _numeric(frame, "statefip").astype("int16"),
            "countyicp": _numeric(frame, "countyicp").astype("int16"),
            "countynhg": _numeric(frame, "countynhg").fillna(0).astype("int32"),
        },
        index=frame.index,
    )
    year = _numeric(frame, "year")
    perwt = _numeric(frame, "perwt").fillna(0).astype("float64")
    hhwt = _numeric(frame, "hhwt").fillna(0).astype("float64")
    slwt = _numeric(frame, "slwt").fillna(0).astype("float64")
    sampleline_weight = perwt.where(~year.eq(1940), slwt.where(slwt.gt(0), 0))

    bpl = _numeric(frame, "bpl")
    mbpl = _numeric(frame, "mbpl")
    fbpl = _numeric(frame, "fbpl")
    native_born = bpl.between(1, 99)
    irish_born = bpl.eq(IRISH_BPL)
    mother_irish = mbpl.eq(IRISH_BPL)
    father_irish = fbpl.eq(IRISH_BPL)
    either_parent_irish = mother_irish | father_irish
    second_generation = native_born & either_parent_irish
    one_irish_parent = second_generation & (mother_irish ^ father_irish)
    two_irish_parents = second_generation & mother_irish & father_irish
    irish_origin = irish_born | second_generation

    measures: dict[str, pd.Series] = {}

    def count(name: str, mask: pd.Series, weight: pd.Series = perwt) -> None:
        measures[name] = weight.where(mask.fillna(False), 0.0)

    def total_and_observed(
        name: str,
        value: pd.Series,
        observed: pd.Series,
        group: pd.Series,
        weight: pd.Series,
    ) -> None:
        valid = group.fillna(False) & observed.fillna(False)
        measures[f"{name}_sum"] = (value.fillna(0) * weight).where(valid, 0.0)
        measures[f"{name}_observed_count"] = weight.where(valid, 0.0)

    count("population", pd.Series(True, index=frame.index))
    count("irish_born_count", irish_born)
    count("second_generation_irish_count", second_generation, sampleline_weight.where(year.eq(1940), perwt))
    count("one_irish_parent_count", one_irish_parent, sampleline_weight.where(year.eq(1940), perwt))
    count("two_irish_parents_count", two_irish_parents, sampleline_weight.where(year.eq(1940), perwt))
    count("irish_origin_count", irish_origin, sampleline_weight.where(year.eq(1940), perwt))
    parent_observed = native_born & mbpl.gt(0) & fbpl.gt(0)
    count("parent_birthplace_observed_native_count", parent_observed, sampleline_weight.where(year.eq(1940), perwt))

    age = _numeric(frame, "age")
    sex = _numeric(frame, "sex")
    marst = _numeric(frame, "marst")
    urban = _numeric(frame, "urban")
    metro = _numeric(frame, "metro")
    city = _numeric(frame, "city")
    lit = _numeric(frame, "lit")
    school = _numeric(frame, "school")
    labforce = _numeric(frame, "labforce")
    empstat = _numeric(frame, "empstat")
    classwkr = _numeric(frame, "classwkr")
    occ1950 = _numeric(frame, "occ1950")
    ind1950 = _numeric(frame, "ind1950")

    subgroups = {
        "all": (pd.Series(True, index=frame.index), perwt),
        "irish_born": (irish_born, perwt),
        "second_generation_irish": (
            second_generation,
            sampleline_weight.where(year.eq(1940), perwt),
        ),
        "irish_origin": (irish_origin, sampleline_weight.where(year.eq(1940), perwt)),
    }
    for prefix, (group, group_weight) in subgroups.items():
        count(f"{prefix}_count", group, group_weight)
        count(f"{prefix}_urban_count", group & urban.eq(2), group_weight)
        count(f"{prefix}_urban_status_observed_count", group & urban.isin((1, 2)), group_weight)
        count(f"{prefix}_metro_resident_count", group & metro.isin((2, 3, 4)), group_weight)
        count(f"{prefix}_metro_status_observed_count", group & metro.between(1, 4), group_weight)
        count(f"{prefix}_identified_city_count", group & city.gt(0), group_weight)
        count(f"{prefix}_male_count", group & sex.eq(1), group_weight)
        count(f"{prefix}_female_count", group & sex.eq(2), group_weight)
        count(f"{prefix}_sex_observed_count", group & sex.isin((1, 2)), group_weight)
        count(f"{prefix}_child_count", group & age.between(0, 17), group_weight)
        count(f"{prefix}_working_age_count", group & age.between(18, 64), group_weight)
        count(f"{prefix}_older_count", group & age.between(65, 140), group_weight)
        count(f"{prefix}_age_observed_count", group & age.between(0, 140), group_weight)
        count(f"{prefix}_married_count", group & marst.eq(1), group_weight)
        count(f"{prefix}_marital_status_observed_count", group & marst.between(1, 6), group_weight)
        count(f"{prefix}_literate_count", group & lit.eq(4), group_weight)
        count(f"{prefix}_literacy_observed_count", group & lit.between(1, 4), group_weight)
        count(f"{prefix}_school_attending_count", group & school.eq(2), group_weight)
        count(f"{prefix}_school_status_observed_count", group & school.isin((1, 2)), group_weight)
        higrade = _numeric(frame, "higrade")
        count(
            f"{prefix}_high_school_or_more_count",
            group & higrade.between(15, 23),
            sampleline_weight,
        )
        count(
            f"{prefix}_education_observed_count",
            group & higrade.between(1, 23),
            sampleline_weight,
        )
        count(f"{prefix}_labor_force_count", group & labforce.eq(2), group_weight)
        count(f"{prefix}_labor_force_status_observed_count", group & labforce.isin((1, 2)), group_weight)
        count(f"{prefix}_employed_count", group & empstat.eq(1), group_weight)
        count(f"{prefix}_unemployed_count", group & empstat.eq(2), group_weight)
        count(f"{prefix}_employment_status_observed_count", group & empstat.between(1, 3), group_weight)
        occupation_observed = occ1950.between(0, 970)
        count(f"{prefix}_musician_music_teacher_count", group & occ1950.eq(57), group_weight)
        count(f"{prefix}_occupation_observed_count", group & occupation_observed, group_weight)
        industry_observed = ind1950.between(1, 899)
        count(f"{prefix}_agriculture_industry_count", group & ind1950.eq(105), group_weight)
        count(f"{prefix}_entertainment_industry_count", group & ind1950.isin((857, 859)), group_weight)
        count(f"{prefix}_industry_observed_count", group & industry_observed, group_weight)
        count(f"{prefix}_self_employed_count", group & classwkr.eq(1), group_weight)
        count(f"{prefix}_wage_worker_count", group & classwkr.eq(2), group_weight)
        count(f"{prefix}_class_worker_observed_count", group & classwkr.isin((1, 2)), group_weight)

        total_and_observed(f"{prefix}_age", age, age.between(0, 140), group, group_weight)
        total_and_observed(
            f"{prefix}_family_size", _numeric(frame, "famsize"), _numeric(frame, "famsize").gt(0), group, group_weight
        )
        total_and_observed(
            f"{prefix}_children_present", _numeric(frame, "nchild"), _numeric(frame, "nchild").between(0, 9), group, group_weight
        )
        for variable in ("occscore", "sei", "presgl", "erscor50", "edscor50", "npboss50"):
            values = _numeric(frame, variable)
            valid = occupation_observed & values.between(0, 100)
            total_and_observed(f"{prefix}_{variable}", values, valid, group, group_weight)

        incwage = _numeric(frame, "incwage")
        income_weight = sampleline_weight.where(year.eq(1940), group_weight)
        total_and_observed(
            f"{prefix}_incwage", incwage, incwage.between(0, 999997), group, income_weight
        )
        nonwage = _numeric(frame, "incnonwg")
        count(
            f"{prefix}_nonwage_income_50plus_count",
            group & nonwage.eq(2),
            income_weight,
        )
        count(
            f"{prefix}_nonwage_income_status_observed_count",
            group & nonwage.isin((1, 2)),
            income_weight,
        )
        weeks = _numeric(frame, "wkswork1")
        total_and_observed(
            f"{prefix}_weeks_worked",
            weeks,
            weeks.between(1, 52),
            group,
            sampleline_weight.where(year.eq(1940), group_weight),
        )
        hours = _numeric(frame, "hrswork1")
        total_and_observed(
            f"{prefix}_hours_worked",
            hours,
            hours.between(1, 98),
            group,
            sampleline_weight.where(year.eq(1940), group_weight),
        )
        realprop = _numeric(frame, "realprop")
        total_and_observed(
            f"{prefix}_realprop", realprop, realprop.between(0, 99999997), group, group_weight
        )
        persprop = _numeric(frame, "persprop")
        total_and_observed(
            f"{prefix}_persprop", persprop, persprop.between(0, 99999997), group, group_weight
        )

    yrimmig = _numeric(frame, "yrimmig")
    arrival_observed = irish_born & yrimmig.between(1790, year)
    count("irish_arrival_year_observed_count", arrival_observed)
    count("irish_recent_arrival_10yr_count", arrival_observed & (year - yrimmig).between(0, 10))
    count("irish_established_arrival_gt10yr_count", arrival_observed & (year - yrimmig).gt(10))
    total_and_observed("irish_arrival_year", yrimmig, arrival_observed, pd.Series(True, index=frame.index), perwt)

    citizen = _numeric(frame, "citizen")
    citizenship_weight = sampleline_weight.where(year.eq(1940), perwt)
    citizenship_observed = irish_born & citizen.between(2, 5)
    count("irish_naturalized_count", citizenship_observed & citizen.eq(2), citizenship_weight)
    count("irish_citizenship_observed_count", citizenship_observed, citizenship_weight)

    mtongued = _numeric(frame, "mtongued")
    languaged = _numeric(frame, "languaged")
    language_weight = sampleline_weight.where(year.eq(1940), perwt)
    count("irish_gaelic_mother_tongue_strict_count", mtongued.isin(IRISH_LANGUAGE_STRICT), language_weight)
    count("irish_gaelic_mother_tongue_broad_count", mtongued.isin(IRISH_LANGUAGE_BROAD), language_weight)
    count("irish_language_spoken_strict_count", languaged.isin(IRISH_LANGUAGE_STRICT), perwt)
    count("irish_language_spoken_broad_count", languaged.isin(IRISH_LANGUAGE_BROAD), perwt)
    speakeng = _numeric(frame, "speakeng")
    count("irish_born_speaks_english_count", irish_born & speakeng.between(2, 6), perwt)
    count("irish_born_english_status_observed_count", irish_born & speakeng.between(1, 6), perwt)

    migrate5 = _numeric(frame, "migrate5")
    count("moved_count_1935_1940", migrate5.isin((2, 3, 4, 8)), sampleline_weight)
    count("migration_status_observed_count_1935_1940", migrate5.between(1, 4), sampleline_weight)
    count("irish_born_moved_count_1935_1940", irish_born & migrate5.isin((2, 3, 4, 8)), sampleline_weight)
    count("irish_born_migration_status_observed_count_1935_1940", irish_born & migrate5.between(1, 4), sampleline_weight)

    household = _numeric(frame, "pernum").eq(1) & _numeric(frame, "gq").isin(HOUSEHOLD_GQ_CODES)
    count("household_count", household, hhwt)
    ownershp = _numeric(frame, "ownershp")
    count("owner_occupied_household_count", household & ownershp.eq(1), hhwt)
    count("tenure_observed_household_count", household & ownershp.isin((1, 2)), hhwt)
    mortgage = _numeric(frame, "mortgage")
    count("mortgaged_household_count", household & mortgage.isin((2, 3, 4)), hhwt)
    count("mortgage_status_observed_household_count", household & mortgage.between(1, 4), hhwt)
    farm = _numeric(frame, "farm")
    count("farm_household_count", household & farm.eq(2), hhwt)
    count("farm_status_observed_household_count", household & farm.isin((1, 2)), hhwt)
    radio = _numeric(frame, "radio30")
    count("radio_household_count_1930", household & radio.eq(2), hhwt)
    count("radio_status_observed_household_count_1930", household & radio.isin((1, 2)), hhwt)
    count("irish_born_reference_person_household_count", household & irish_born, hhwt)
    count("second_generation_irish_reference_person_household_count", household & second_generation, hhwt)
    count(
        "irish_born_reference_person_radio_household_count_1930",
        household & irish_born & radio.eq(2),
        hhwt,
    )
    count(
        "irish_born_reference_person_radio_status_observed_count_1930",
        household & irish_born & radio.isin((1, 2)),
        hhwt,
    )
    count(
        "second_generation_irish_reference_person_radio_household_count_1930",
        household & second_generation & radio.eq(2),
        hhwt,
    )
    count(
        "second_generation_irish_reference_person_radio_status_observed_count_1930",
        household & second_generation & radio.isin((1, 2)),
        hhwt,
    )

    valueh = _numeric(frame, "valueh")
    total_and_observed(
        "housing_value", valueh, household & valueh.between(0, 9_999_997), pd.Series(True, index=frame.index), hhwt
    )
    rent30 = _numeric(frame, "rent30")
    total_and_observed(
        "rent_1930", rent30, household & rent30.between(1, 9_997), pd.Series(True, index=frame.index), hhwt
    )

    working = pd.concat([keys, pd.DataFrame(measures, index=frame.index)], axis=1)
    return working.groupby(list(KEY_COLUMNS), as_index=False, observed=True).sum()


class MatrixAccumulator:
    """Accumulate many small county partials without concatenating all parts."""

    def __init__(self, measure_columns: list[str]):
        self.measure_columns = measure_columns
        self.keys: list[tuple[int, ...]] = []
        self.positions: dict[tuple[int, ...], int] = {}
        self.values = np.zeros((4096, len(measure_columns)), dtype="float64")

    def add(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        indexes = np.empty(len(frame), dtype="int64")
        for offset, key in enumerate(frame[list(KEY_COLUMNS)].itertuples(index=False, name=None)):
            normalized = tuple(int(value) for value in key)
            position = self.positions.get(normalized)
            if position is None:
                position = len(self.keys)
                self.positions[normalized] = position
                self.keys.append(normalized)
                if position == len(self.values):
                    self.values = np.vstack(
                        [self.values, np.zeros_like(self.values)]
                    )
            indexes[offset] = position
        np.add.at(
            self.values,
            indexes,
            frame[self.measure_columns].to_numpy(dtype="float64", na_value=0),
        )

    def frame(self) -> pd.DataFrame:
        keys = pd.DataFrame(self.keys, columns=KEY_COLUMNS)
        values = pd.DataFrame(
            self.values[: len(self.keys)], columns=self.measure_columns
        )
        return pd.concat([keys, values], axis=1)


def combine_parts(parts_dir: Path) -> pd.DataFrame:
    paths = sorted(parts_dir.glob("part-*.parquet"))
    if not paths:
        raise RuntimeError("no aggregate parts were written")
    first = pd.read_parquet(paths[0])
    measures = [column for column in first.columns if column not in KEY_COLUMNS]
    accumulator = MatrixAccumulator(measures)
    accumulator.add(first)
    for path in paths[1:]:
        frame = pd.read_parquet(path)
        if [column for column in frame.columns if column not in KEY_COLUMNS] != measures:
            raise RuntimeError(f"aggregate schema changed in {path}")
        accumulator.add(frame)
    return accumulator.frame().sort_values(list(KEY_COLUMNS)).reset_index(drop=True)


def _download_json(url: str, retries: int = 6) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "historical-panel/1.0"})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except Exception:
            if attempt == retries:
                raise
            time.sleep(min(30, 2**attempt))
    raise AssertionError("unreachable")


def historical_county_lookup(
    year: int,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    cache_path = cache_dir / f"county_lookup_{year}.parquet" if cache_dir else None
    if cache_path is not None and cache_path.exists():
        return pd.read_parquet(cache_path)
    layer = LAYER_BY_YEAR[year]
    count_url = f"{ARCGIS_ROOT}/{layer}/query?" + urllib.parse.urlencode(
        {"where": "1=1", "returnCountOnly": "true", "f": "json"}
    )
    expected = int(_download_json(count_url)["count"])
    rows: list[dict[str, object]] = []
    for offset in range(0, expected, 2000):
        params = {
            "where": "1=1",
            "outFields": "GISJOIN,ICPSRSTI,ICPSRCTYI,NHGISNAM,STATENAM",
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": str(offset),
            "resultRecordCount": "2000",
        }
        url = f"{ARCGIS_ROOT}/{layer}/query?" + urllib.parse.urlencode(params)
        payload = _download_json(url)
        if "features" not in payload:
            raise RuntimeError(f"NHGIS county lookup failed for {year}: {payload}")
        rows.extend(feature["attributes"] for feature in payload["features"])
    if len(rows) != expected:
        raise RuntimeError(
            f"NHGIS county lookup returned {len(rows)} of {expected} rows for {year}"
        )
    result = pd.DataFrame(rows).rename(
        columns={
            "GISJOIN": "source_gisjoin",
            "ICPSRSTI": "stateicp",
            "ICPSRCTYI": "countyicp",
            "NHGISNAM": "source_county_name",
            "STATENAM": "source_state_name",
        }
    )
    result["census_year"] = year
    # NHGIS uses the shared (0, 0) ICPSR placeholder for multiple no-data or
    # unorganized polygons in some early layers.  Person records with either
    # zero code are excluded upstream, so retaining these placeholders would
    # create false duplicate join keys without making any record matchable.
    result["stateicp"] = pd.to_numeric(result["stateicp"], errors="coerce")
    result["countyicp"] = pd.to_numeric(result["countyicp"], errors="coerce")
    result = result[result["stateicp"].gt(0) & result["countyicp"].gt(0)].copy()
    if cache_path is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
        result.to_parquet(temporary, index=False, compression="zstd")
        os.replace(temporary, cache_path)
    return result


def attach_source_geography(
    frame: pd.DataFrame,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    lookup = pd.concat(
        [historical_county_lookup(year, cache_dir) for year in CENSUS_YEARS],
        ignore_index=True,
    )
    gisjoin_keys = ["census_year", "source_gisjoin"]
    if lookup.duplicated(gisjoin_keys).any():
        raise RuntimeError("NHGIS lookup has duplicate year/GISJOIN keys")

    # IPUMS explicitly documents COUNTYNHG as the direct bridge to NHGIS for
    # 1850-1930: GISJOIN is the seven-character code with a leading "G".
    # COUNTYNHG is unavailable in 1940, when STATEICP + COUNTYICP is the
    # documented match instead.
    early = frame[frame["census_year"].lt(1940)].copy()
    early_direct = early[early["countynhg"].gt(0)].copy()
    early_direct["source_gisjoin"] = (
        "G"
        + early_direct["countynhg"].astype("int64").astype("string").str.zfill(7)
    )
    early_direct = early_direct.merge(
        lookup[
            [
                "census_year",
                "source_gisjoin",
                "source_county_name",
                "source_state_name",
            ]
        ],
        on=gisjoin_keys,
        how="left",
        validate="many_to_one",
    )

    late_keys = ["census_year", "stateicp", "countyicp"]
    icpsr_lookup = lookup.copy()
    if icpsr_lookup.duplicated(late_keys).any():
        raise RuntimeError("NHGIS lookup has duplicate ICPSR county keys")
    early_fallback = early[early["countynhg"].le(0)].merge(
        icpsr_lookup[
            late_keys
            + ["source_gisjoin", "source_county_name", "source_state_name"]
        ],
        on=late_keys,
        how="left",
        validate="many_to_one",
    )
    lookup_1940 = lookup[lookup["census_year"].eq(1940)]
    if lookup_1940.duplicated(late_keys).any():
        raise RuntimeError("1940 NHGIS lookup has duplicate ICPSR county keys")
    late = frame[frame["census_year"].eq(1940)].merge(
        lookup_1940[
            late_keys
            + ["source_gisjoin", "source_county_name", "source_state_name"]
        ],
        on=late_keys,
        how="left",
        validate="many_to_one",
    )
    result = pd.concat([early_direct, early_fallback, late], ignore_index=True)
    result["source_geography_matched"] = result["source_county_name"].notna()
    return result.sort_values(list(KEY_COLUMNS)).reset_index(drop=True)


def consolidate_source_counties(source_keys: pd.DataFrame) -> pd.DataFrame:
    """Collapse alternative source keys onto one official year/GISJOIN row."""

    matched = source_keys[source_keys["source_geography_matched"]].copy()
    excluded = set(KEY_COLUMNS) | {
        "source_gisjoin", "source_county_name", "source_state_name",
        "source_geography_matched",
    }
    measures = [column for column in matched.columns if column not in excluded]
    groups = ["census_year", "source_gisjoin"]
    values = matched.groupby(groups, as_index=False, observed=True)[measures].sum(min_count=1)
    metadata = matched.groupby(groups, as_index=False, observed=True).agg(
        sample=("sample", "first"),
        source_county_name=("source_county_name", "first"),
        source_state_name=("source_state_name", "first"),
        source_key_rows=("source_gisjoin", "size"),
    )
    return (
        metadata.merge(values, on=groups, how="left", validate="one_to_one")
        .sort_values(groups)
        .reset_index(drop=True)
    )


def harmonize_to_1940(source: pd.DataFrame, crosswalk_path: Path) -> pd.DataFrame:
    crosswalk = pd.read_parquet(crosswalk_path)
    merged = crosswalk.merge(
        source,
        left_on=["source_year", "source_gisjoin"],
        right_on=["census_year", "source_gisjoin"],
        how="left",
        validate="many_to_one",
    )
    measures = [
        column
        for column in source.columns
        if column not in {
            "census_year", "sample", "source_gisjoin", "source_county_name",
            "source_state_name", "source_key_rows",
        }
    ]
    for column in measures:
        merged[column] = merged[column] * merged["source_area_weight"]
    group_columns = [
        "county_1940_id", "state_code_1940", "county_code_1940",
        "county_name_1940", "state_name_1940", "census_year",
    ]
    result = merged.groupby(group_columns, as_index=False, observed=True)[measures].sum(min_count=1)
    result["ipums_geography_status"] = "area_harmonized_from_person_microdata"
    return result.sort_values(["county_1940_id", "census_year"]).reset_index(drop=True)


def write_allocation_audit(
    source: pd.DataFrame,
    crosswalk_path: Path,
    output_path: Path,
) -> None:
    """Record how completely every source polygon overlaps the 1940 target."""

    crosswalk = pd.read_parquet(crosswalk_path)
    weights = (
        crosswalk.groupby(["source_year", "source_gisjoin"], as_index=False)[
            "source_area_weight"
        ]
        .sum()
        .rename(
            columns={
                "source_year": "census_year",
                "source_area_weight": "allocation_weight_sum",
            }
        )
    )
    audit = source[
        [
            "census_year", "source_gisjoin", "source_state_name",
            "source_county_name", "population", "irish_born_count",
        ]
    ].merge(weights, on=["census_year", "source_gisjoin"], how="left")
    audit["allocation_weight_sum"] = audit["allocation_weight_sum"].fillna(0)
    audit["unallocated_population"] = audit["population"] * (
        1 - audit["allocation_weight_sum"]
    )
    audit["unallocated_irish_born"] = audit["irish_born_count"] * (
        1 - audit["allocation_weight_sum"]
    )
    audit["allocation_status"] = np.select(
        [
            audit["allocation_weight_sum"].eq(0),
            audit["allocation_weight_sum"].lt(0.999999),
            audit["allocation_weight_sum"].gt(1.000001),
        ],
        ["no_target_overlap", "partial_target_overlap", "overweight"],
        default="complete_target_overlap",
    )
    audit.to_csv(output_path, index=False)


def write_coverage(frame: pd.DataFrame, path: Path) -> None:
    rows = []
    measures = [column for column in frame.columns if column not in KEY_COLUMNS]
    for year, group in frame.groupby("census_year"):
        for column in measures:
            if pd.api.types.is_numeric_dtype(group[column]):
                rows.append(
                    {
                        "census_year": int(year),
                        "variable": column,
                        "nonzero_counties": int(group[column].fillna(0).ne(0).sum()),
                        "sum": float(group[column].sum()),
                    }
                )
    pd.DataFrame(rows).to_csv(path, index=False)


def aggregate_extract(
    *,
    data_path: Path,
    ddi_path: Path,
    work_dir: Path,
    processed_dir: Path,
    crosswalk_path: Path,
    chunk_rows: int,
    workers: int,
    resume: bool,
) -> dict[str, object]:
    if workers <= 0:
        raise ValueError("workers must be positive")
    ddi = read_ddi(ddi_path)
    missing = sorted(set(PARSE_FIELDS).difference(v.name for v in ddi.variables))
    if missing:
        raise RuntimeError(f"new IPUMS DDI is missing required aggregation fields: {missing}")
    validate_ddi_for_data(ddi, data_path)
    work_dir.mkdir(parents=True, exist_ok=True)
    parts_dir = work_dir / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = work_dir / "aggregation_manifest.json"
    signature = {
        "data_path": str(data_path.resolve()),
        "data_size": data_path.stat().st_size,
        "ddi_path": str(ddi_path.resolve()),
        "ddi_sha256": sha256_file(ddi_path),
        "record_width": ddi.record_width,
        "chunk_rows": chunk_rows,
        "full_count_samples": {
            str(year): sample for year, sample in FULL_COUNT_SAMPLES.items()
        },
    }
    if manifest_path.exists():
        if not resume:
            raise FileExistsError(f"{manifest_path} exists; pass --resume")
        manifest = json.loads(manifest_path.read_text())
        for key, expected in signature.items():
            if manifest.get(key) != expected:
                raise RuntimeError(f"resume mismatch for {key}")
    else:
        manifest = {
            **signature,
            "complete": False,
            "completed_chunks": [],
            "source_rows_scanned": 0,
            "selected_rows_aggregated": 0,
            "elapsed_seconds": 0.0,
        }
        _atomic_json(manifest_path, manifest)

    completed = set(int(value) for value in manifest["completed_chunks"])
    stride = ddi.record_width + 1
    run_started = time.monotonic()
    previous_elapsed = float(manifest["elapsed_seconds"])

    def commit_chunk(
        chunk_index: int,
        partial: pd.DataFrame,
        source_rows: int,
        selected_rows: int,
    ) -> None:
        temporary = parts_dir / f"part-{chunk_index:06d}.parquet.tmp"
        final = parts_dir / f"part-{chunk_index:06d}.parquet"
        partial.to_parquet(temporary, index=False, compression="zstd")
        os.replace(temporary, final)
        manifest["source_rows_scanned"] += source_rows
        manifest["selected_rows_aggregated"] += selected_rows
        completed.add(chunk_index)
        manifest["completed_chunks"] = sorted(completed)
        manifest["elapsed_seconds"] = previous_elapsed + time.monotonic() - run_started
        _atomic_json(manifest_path, manifest)
        if chunk_index % 10 == 0:
            print(
                f"chunk={chunk_index} rows={manifest['source_rows_scanned']:,} "
                f"selected={manifest['selected_rows_aggregated']:,}",
                flush=True,
            )

    if not manifest.get("raw_complete", False):
        with gzip.open(data_path, "rb") as handle:
            with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
                pending: dict[concurrent.futures.Future, int] = {}

                def collect(*, all_done: bool = False) -> None:
                    if not pending:
                        return
                    return_when = (
                        concurrent.futures.ALL_COMPLETED
                        if all_done
                        else concurrent.futures.FIRST_COMPLETED
                    )
                    done, _ = concurrent.futures.wait(pending, return_when=return_when)
                    for future in done:
                        index = pending.pop(future)
                        partial, source_rows, selected_rows = future.result()
                        commit_chunk(index, partial, source_rows, selected_rows)

                chunk_index = 0
                while block := handle.read(stride * chunk_rows):
                    if len(block) % stride:
                        raise RuntimeError("IPUMS gzip ended inside a fixed-width record")
                    if chunk_index not in completed:
                        while len(pending) >= workers * 2:
                            collect()
                        pending[executor.submit(_parse_and_aggregate, block, ddi)] = chunk_index
                    chunk_index += 1
                collect(all_done=True)
        manifest["raw_complete"] = True
        manifest["total_chunks"] = chunk_index
        manifest["elapsed_seconds"] = previous_elapsed + time.monotonic() - run_started
        _atomic_json(manifest_path, manifest)
    else:
        expected_chunks = set(range(int(manifest["total_chunks"])))
        if completed != expected_chunks:
            raise RuntimeError("raw-complete manifest does not contain every chunk")

    combined_path = work_dir / "combined_source_county_decade.parquet"
    if combined_path.exists():
        combined = pd.read_parquet(combined_path)
    else:
        combined = combine_parts(parts_dir)
        temporary = combined_path.with_suffix(combined_path.suffix + ".tmp")
        combined.to_parquet(temporary, index=False, compression="zstd")
        os.replace(temporary, combined_path)
    source_keys = attach_source_geography(
        combined,
        work_dir / "geography_lookup",
    )
    processed_dir.mkdir(parents=True, exist_ok=True)
    source_keys_path = processed_dir / "ipums_source_key_decade.parquet"
    source_keys.to_parquet(source_keys_path, index=False, compression="zstd")
    source = consolidate_source_counties(source_keys)
    source_path = processed_dir / "ipums_source_county_decade.parquet"
    source.to_parquet(source_path, index=False, compression="zstd")
    write_allocation_audit(
        source,
        crosswalk_path,
        processed_dir / "ipums_geography_allocation_audit.csv",
    )
    source_keys.loc[
        ~source_keys["source_geography_matched"],
        [
            "census_year", "sample", "stateicp", "statefip", "countyicp",
            "countynhg", "source_gisjoin", "population", "irish_born_count",
        ],
    ].to_csv(processed_dir / "ipums_unmatched_source_geographies.csv", index=False)
    harmonized = harmonize_to_1940(source, crosswalk_path)
    panel_path = processed_dir / "ipums_county_decade_1940.parquet"
    harmonized.to_parquet(panel_path, index=False, compression="zstd")
    harmonized.to_csv(
        processed_dir / "ipums_county_decade_1940.csv.gz",
        index=False,
        compression="gzip",
    )
    harmonized.to_csv(
        processed_dir / "ipums_county_decade_1940.csv",
        index=False,
    )
    write_coverage(
        harmonized,
        processed_dir / "ipums_variable_coverage.csv",
    )
    manifest["complete"] = True
    manifest["elapsed_seconds"] = previous_elapsed + time.monotonic() - run_started
    manifest["source_key_rows"] = len(source_keys)
    manifest["unmatched_source_key_rows"] = int(
        (~source_keys["source_geography_matched"]).sum()
    )
    manifest["source_county_rows"] = len(source)
    manifest["harmonized_rows"] = len(harmonized)
    manifest["data_sha256"] = sha256_file(data_path)
    _atomic_json(manifest_path, manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("usa_00002.dat.gz"))
    parser.add_argument("--ddi", type=Path, default=Path("usa_00002.xml"))
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("historical_panel/data/ipums_aggregate_work"),
    )
    parser.add_argument(
        "--processed-dir",
        type=Path,
        default=Path("historical_panel/data/processed"),
    )
    parser.add_argument(
        "--crosswalk",
        type=Path,
        default=Path("historical_panel/data/processed/county_crosswalk.parquet"),
    )
    parser.add_argument("--chunk-rows", type=int, default=250_000)
    parser.add_argument("--workers", type=int, default=min(32, os.cpu_count() or 1))
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    output = aggregate_extract(
        data_path=arguments.data,
        ddi_path=arguments.ddi,
        work_dir=arguments.work_dir,
        processed_dir=arguments.processed_dir,
        crosswalk_path=arguments.crosswalk,
        chunk_rows=arguments.chunk_rows,
        workers=arguments.workers,
        resume=arguments.resume,
    )
    print(json.dumps(output, indent=2, sort_keys=True))
