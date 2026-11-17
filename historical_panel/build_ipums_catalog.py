"""Build a complete collaborator-facing catalog for the IPUMS panel columns."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
PANEL = ROOT / "data/processed/ipums_county_decade_1940.parquet"
OUTPUT = ROOT / "ipums_variable_catalog.csv"

IDENTIFIERS = {
    "county_1940_id": ("Stable 1940 county GISJOIN identifier.", "1940 NHGIS geometry"),
    "state_code_1940": ("State component of the 1940 analysis identifier.", "1940 NHGIS geometry"),
    "county_code_1940": ("County component of the 1940 analysis identifier.", "1940 NHGIS geometry"),
    "county_name_1940": ("County name on the fixed 1940 analysis geography.", "1940 NHGIS geometry"),
    "state_name_1940": ("State name on the fixed 1940 analysis geography.", "1940 NHGIS geometry"),
    "census_year": ("Decennial census year; 1890 is absent.", "YEAR"),
    "ipums_geography_status": (
        "Construction status for the harmonized IPUMS row.",
        "NHGIS polygon crosswalk",
    ),
}

SPECIAL = {
    "population": ("Area-allocated total resident population.", "PERWT"),
    "irish_born_count": ("Residents born in Ireland (BPL == 414).", "BPL, PERWT"),
    "second_generation_irish_count": (
        "U.S.-born residents with at least one Irish-born parent.",
        "BPL, MBPL, FBPL, PERWT/SLWT",
    ),
    "one_irish_parent_count": (
        "U.S.-born residents with exactly one Irish-born parent.",
        "BPL, MBPL, FBPL, PERWT/SLWT",
    ),
    "two_irish_parents_count": (
        "U.S.-born residents with two Irish-born parents.",
        "BPL, MBPL, FBPL, PERWT/SLWT",
    ),
    "irish_origin_count": (
        "Irish-born plus U.S.-born residents with at least one Irish-born parent.",
        "BPL, MBPL, FBPL, PERWT/SLWT",
    ),
    "parent_birthplace_observed_native_count": (
        "U.S.-born residents for whom both parental birthplaces are observed.",
        "BPL, MBPL, FBPL, PERWT/SLWT",
    ),
}

GROUPS = (
    ("second_generation_irish_", "second-generation Irish residents"),
    ("irish_origin_", "Irish-origin residents"),
    ("irish_born_", "Irish-born residents"),
    ("all_", "all residents"),
)

SOURCE_PATTERNS = (
    ("urban", "URBAN"),
    ("metro", "METRO"),
    ("identified_city", "CITY"),
    ("male", "SEX"), ("female", "SEX"), ("sex_", "SEX"),
    ("working_age", "AGE"), ("child", "AGE/NCHILD"), ("older", "AGE"),
    ("age", "AGE"),
    ("married", "MARST"), ("marital", "MARST"),
    ("literate", "LIT"), ("literacy", "LIT"),
    ("school_attending", "SCHOOL"), ("school_status", "SCHOOL"),
    ("high_school", "HIGRADE"), ("education_observed", "HIGRADE"),
    ("labor_force", "LABFORCE"),
    ("unemployed", "EMPSTAT"), ("employed", "EMPSTAT"),
    ("employment_status", "EMPSTAT"),
    ("musician_music_teacher", "OCC1950"),
    ("occupation_observed", "OCC1950"),
    ("agriculture_industry", "IND1950"),
    ("entertainment_industry", "IND1950"),
    ("industry_observed", "IND1950"),
    ("self_employed", "CLASSWKR"), ("wage_worker", "CLASSWKR"),
    ("class_worker", "CLASSWKR"),
    ("family_size", "FAMSIZE"), ("children_present", "NCHILD"),
    ("occscore", "OCCSCORE, OCC1950"), ("presgl", "PRESGL, OCC1950"),
    ("erscor50", "ERSCOR50, OCC1950"), ("edscor50", "EDSCOR50, OCC1950"),
    ("npboss50", "NPBOSS50, OCC1950"), ("sei", "SEI, OCC1950"),
    ("nonwage_income", "INCNONWG"), ("incwage", "INCWAGE"),
    ("weeks_worked", "WKSWORK1"), ("hours_worked", "HRSWORK1"),
    ("realprop", "REALPROP"), ("persprop", "PERSPROP"),
    ("arrival", "YRIMMIG, YRSUSA1, YRSUSA2"),
    ("naturalized", "CITIZEN"), ("citizenship", "CITIZEN"),
    ("mother_tongue", "MTONGUED"), ("language_spoken", "LANGUAGED"),
    ("speaks_english", "SPEAKENG"), ("english_status", "SPEAKENG"),
    ("migration_status", "MIGRATE5"), ("moved", "MIGRATE5"),
    ("owner_occupied", "OWNERSHP, HHWT"), ("tenure", "OWNERSHP, HHWT"),
    ("mortgage", "MORTGAGE, HHWT"), ("farm_household", "FARM, HHWT"),
    ("farm_status", "FARM, HHWT"), ("radio", "RADIO30, HHWT"),
    ("housing_value", "VALUEH, HHWT"), ("rent_1930", "RENT30, RENT, HHWT"),
    ("household", "PERNUM, GQ, HHWT"),
)


def group_and_measure(column: str) -> tuple[str, str]:
    for prefix, label in GROUPS:
        if column.startswith(prefix):
            return label, column.removeprefix(prefix)
    return "applicable residents or households", column


def source_fields(column: str) -> str:
    if column in SPECIAL:
        return SPECIAL[column][1]
    for token, fields in SOURCE_PATTERNS:
        if token in column:
            return fields
    if column.endswith("_count"):
        return "PERWT and fields named by the measure"
    return "See aggregate_ipums.py"


def weighting(column: str, fields: str) -> str:
    if "HHWT" in fields:
        return "HHWT on one eligible record per household"
    sample_line_tokens = (
        "second_generation", "irish_origin", "parent_birthplace", "high_school",
        "education_", "incwage", "nonwage", "mother_tongue", "language_spoken",
        "english_", "naturalized", "citizenship", "migration_status", "moved_",
    )
    if any(token in column for token in sample_line_tokens):
        return "SLWT for applicable 1940 sample-line measures; PERWT otherwise"
    return "PERWT"


def definition(column: str) -> str:
    if column in SPECIAL:
        return SPECIAL[column][0]
    group, measure = group_and_measure(column)
    readable = measure.replace("_", " ")
    if measure.endswith("_observed_count") or "status_observed_count" in measure:
        concept = readable.removesuffix(" observed count")
        return f"Area-allocated weighted number of {group} with a valid observed value for {concept}."
    if measure.endswith("_sum"):
        concept = readable.removesuffix(" sum")
        return f"Area-allocated weighted sum of {concept} among {group}; divide by its observed-count column for a mean."
    if measure.endswith("_count") or column.endswith("_count"):
        return f"Area-allocated weighted count for {group}: {readable.removesuffix(' count')}."
    return f"Area-allocated IPUMS measure for {group}: {readable}."


def build_catalog(panel_path: Path = PANEL, output_path: Path = OUTPUT) -> pd.DataFrame:
    panel = pd.read_parquet(panel_path)
    rows = []
    for position, column in enumerate(panel.columns, start=1):
        if column in IDENTIFIERS:
            description, fields = IDENTIFIERS[column]
            role = "identifier" if column != "ipums_geography_status" else "status"
            statistic = role
            units = "text or code" if column != "census_year" else "year"
            years = "1850-1940 excluding 1890"
            weight = "not applicable"
        else:
            description = definition(column)
            fields = source_fields(column)
            role = "measure"
            statistic = (
                "observed-universe denominator"
                if "observed_count" in column or "status_observed_count" in column
                else "weighted sum"
                if column.endswith("_sum")
                else "weighted count"
            )
            units = "area-allocated weighted value" if statistic == "weighted sum" else "area-allocated persons or households"
            numeric = pd.to_numeric(panel[column], errors="coerce")
            years = ";".join(
                str(int(year))
                for year in sorted(panel.loc[numeric.fillna(0).ne(0), "census_year"].dropna().unique())
            )
            weight = weighting(column, fields)
        rows.append(
            {
                "column_position": position,
                "column_name": column,
                "role": role,
                "statistic": statistic,
                "definition": description,
                "source_fields": fields,
                "weighting": weight,
                "units": units,
                "years_with_nonzero_values": years,
                "geography": "fixed 1940 county; pre-1940 values area allocated",
                "availability_caution": "Nonzero years are empirical coverage, not a substitute for the IPUMS universe statement; use the paired observed-count denominator.",
            }
        )
    result = pd.DataFrame(rows)
    result.to_csv(output_path, index=False)
    return result


if __name__ == "__main__":
    catalog = build_catalog()
    print(f"wrote {len(catalog)} rows to {OUTPUT}")
