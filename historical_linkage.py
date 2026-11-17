"""Dependency-light utilities for historical person-record linkage.

The functions in this module deliberately separate candidate generation from
candidate scoring.  Candidate generation is the part that must scale with a
complete-count census; fuzzy string comparisons should only be run on the
small set of pairs returned by blocking.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence

import numpy as np
import pandas as pd


_NON_LETTERS = re.compile(r"[^A-Z]+")
_SURNAME_PARTICLES = {"O", "MC", "MAC", "FITZ", "DE", "DEL", "VAN", "VON", "ST"}
_NAME_TITLES = {
    "DR",
    "FATHER",
    "MASTER",
    "MISS",
    "MR",
    "MRS",
    "MS",
    "REV",
    "REVEREND",
}
_NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV"}
_TITLE_SEX = {
    "FATHER": 1,
    "MASTER": 1,
    "MR": 1,
    "REV": 1,
    "REVEREND": 1,
    "MISS": 2,
    "MRS": 2,
    "MS": 2,
}
_VOWELS = frozenset("AEIOU")


def normalize_name(value: object) -> str:
    """Return an uppercase, ASCII, letters-only representation of a name."""

    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return _NON_LETTERS.sub("", value.upper())


def split_passenger_name(value: object) -> tuple[str, str, str]:
    """Split a passenger full name into normalized first, middle initial, surname.

    Manifest names in this project are generally in given-name-first order.  A
    small particle rule preserves forms such as ``Mc Kenna`` and ``O Brien``.
    """

    if not isinstance(value, str):
        return "", "", ""
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    parts = re.findall(r"[A-Za-z]+", ascii_value.upper())
    while parts and parts[0] in _NAME_TITLES:
        parts.pop(0)
    while parts and parts[-1] in _NAME_SUFFIXES:
        parts.pop()
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", ""

    surname_start = len(parts) - 1
    if len(parts) >= 3 and parts[-2] in _SURNAME_PARTICLES:
        surname_start -= 1
    surname = "".join(parts[surname_start:])
    given = parts[0]
    middle = parts[1:surname_start]
    return given, (middle[0][0] if middle else ""), surname


def split_given_name(value: object) -> tuple[str, str]:
    """Split a census given-name field into first name and middle initial."""

    if not isinstance(value, str):
        return "", ""
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    parts = re.findall(r"[A-Za-z]+", ascii_value.upper())
    return (parts[0], parts[1][0] if len(parts) > 1 else "") if parts else ("", "")


def nysiis(value: object) -> str:
    """Return a compact NYSIIS-style phonetic key.

    This is the standard New York State Identification and Intelligence System
    transformation, implemented locally so the prototype has no phonetic-name
    package dependency.
    """

    name = normalize_name(value)
    if not name:
        return ""

    if name.startswith("MAC"):
        name = "MCC" + name[3:]
    elif name.startswith("KN"):
        name = "NN" + name[2:]
    elif name.startswith("K"):
        name = "C" + name[1:]
    elif name.startswith(("PH", "PF")):
        name = "FF" + name[2:]
    elif name.startswith("SCH"):
        name = "SSS" + name[3:]

    for suffix in ("EE", "IE"):
        if name.endswith(suffix):
            name = name[:-2] + "Y"
            break
    else:
        for suffix in ("DT", "RT", "RD", "NT", "ND"):
            if name.endswith(suffix):
                name = name[:-2] + "D"
                break

    key = name[0]
    i = 1
    while i < len(name):
        previous = key[-1]
        current = name[i]
        following = name[i + 1] if i + 1 < len(name) else ""

        if name[i : i + 2] == "EV":
            replacement = "AF"
            i += 2
        elif current in _VOWELS:
            replacement = "A"
            i += 1
        elif current == "Q":
            replacement = "G"
            i += 1
        elif current == "Z":
            replacement = "S"
            i += 1
        elif current == "M":
            replacement = "N"
            i += 1
        elif name[i : i + 2] == "KN":
            replacement = "N"
            i += 2
        elif current == "K":
            replacement = "C"
            i += 1
        elif name[i : i + 3] == "SCH":
            replacement = "SSS"
            i += 3
        elif name[i : i + 2] == "PH":
            replacement = "FF"
            i += 2
        elif current == "H":
            replacement = previous if previous not in _VOWELS or following not in _VOWELS else "H"
            i += 1
        elif current == "W" and previous in _VOWELS:
            replacement = previous
            i += 1
        else:
            replacement = current
            i += 1

        for character in replacement:
            if character != key[-1]:
                key += character

    if key.endswith("S"):
        key = key[:-1]
    if key.endswith("AY"):
        key = key[:-2] + "Y"
    if key.endswith("A"):
        key = key[:-1]
    return key


def jaro_winkler_similarity(left: object, right: object, prefix_scale: float = 0.1) -> float:
    """Compute Jaro-Winkler similarity in the conventional 0--1 direction."""

    left = normalize_name(left)
    right = normalize_name(right)
    if left == right:
        return 1.0 if left else 0.0
    if not left or not right:
        return 0.0

    radius = max(len(left), len(right)) // 2 - 1
    radius = max(radius, 0)
    left_matches = [False] * len(left)
    right_matches = [False] * len(right)

    matches = 0
    for i, character in enumerate(left):
        start = max(0, i - radius)
        stop = min(i + radius + 1, len(right))
        for j in range(start, stop):
            if not right_matches[j] and character == right[j]:
                left_matches[i] = True
                right_matches[j] = True
                matches += 1
                break
    if not matches:
        return 0.0

    left_ordered = [character for character, matched in zip(left, left_matches) if matched]
    right_ordered = [character for character, matched in zip(right, right_matches) if matched]
    transpositions = sum(a != b for a, b in zip(left_ordered, right_ordered)) / 2
    jaro = (
        matches / len(left)
        + matches / len(right)
        + (matches - transpositions) / matches
    ) / 3

    prefix = 0
    for a, b in zip(left[:4], right[:4]):
        if a != b:
            break
        prefix += 1
    return jaro + prefix * prefix_scale * (1 - jaro)


def add_name_features(
    frame: pd.DataFrame,
    *,
    first_col: str,
    last_col: str,
    year_col: str,
    age_col: str,
    sex_col: str,
    birthplace_col: str,
    id_col: str,
) -> pd.DataFrame:
    """Select and standardize the fields used by the blocking prototype."""

    result = pd.DataFrame(index=frame.index)
    result["record_id"] = frame[id_col].to_numpy()
    result["first_raw"] = frame[first_col].fillna("").astype(str)
    result["last_raw"] = frame[last_col].fillna("").astype(str)
    parsed_given = result["first_raw"].map(split_given_name)
    result["first_norm"] = parsed_given.map(lambda value: value[0])
    result["middle_initial"] = parsed_given.map(lambda value: value[1])
    result["last_norm"] = result["last_raw"].map(normalize_name)
    result["first_nysiis"] = result["first_norm"].map(nysiis)
    result["last_nysiis"] = result["last_norm"].map(nysiis)
    result["first_initial"] = result["first_norm"].str[:1]
    result["last_initial"] = result["last_norm"].str[:1]
    result["census_year"] = pd.to_numeric(frame[year_col], errors="coerce")
    result["age"] = pd.to_numeric(frame[age_col], errors="coerce")
    result["birth_year"] = (result["census_year"] - result["age"]).round().astype("Int16")
    result["sex"] = pd.to_numeric(frame[sex_col], errors="coerce").astype("Int8")
    result["birthplace"] = pd.to_numeric(frame[birthplace_col], errors="coerce").astype("Int16")
    return result.reset_index(drop=True)


def prepare_passengers(frame: pd.DataFrame) -> pd.DataFrame:
    """Prepare project passenger records for census blocking."""

    parsed = frame["Name"].map(split_passenger_name)
    result = pd.DataFrame(index=frame.index)
    result["record_id"] = np.arange(len(frame), dtype=np.int64)
    result["full_name"] = frame["Name"].fillna("").astype(str)
    result["first_norm"] = parsed.map(lambda value: value[0])
    result["middle_initial"] = parsed.map(lambda value: value[1])
    result["last_norm"] = parsed.map(lambda value: value[2])
    result["first_raw"] = result["first_norm"]
    result["last_raw"] = result["last_norm"]
    result["first_nysiis"] = result["first_norm"].map(nysiis)
    result["last_nysiis"] = result["last_norm"].map(nysiis)
    result["first_initial"] = result["first_norm"].str[:1]
    result["last_initial"] = result["last_norm"].str[:1]
    result["birth_year"] = pd.to_numeric(frame["source_year"], errors="coerce").astype("Int16")
    result["arrival_year"] = pd.to_numeric(
        frame["Arrival Date"].astype(str).str.extract(r"(\d{4})", expand=False), errors="coerce"
    ).astype("Int16")
    gender = frame["Gender"].fillna("").astype(str).str.upper().str[:1]
    explicit_sex = gender.map({"M": 1, "F": 2})
    leading_token = (
        frame["Name"]
        .fillna("")
        .astype(str)
        .str.extract(r"^\s*([A-Za-z]+)", expand=False)
        .str.upper()
    )
    result["sex"] = explicit_sex.fillna(leading_token.map(_TITLE_SEX)).astype("Int8")
    # More than 98% of records are Irish-born.  The small non-Irish remainder is
    # deliberately left unclassified instead of forcing it into the Irish block.
    birthplace = frame["Birth Place"].fillna("").astype(str).str.upper()
    is_irish = birthplace.str.contains(r"IRELAND|\bEIRE\b|\bIRNO\b|\bIRFR\b", regex=True)
    result["birthplace"] = pd.Series(pd.NA, index=frame.index, dtype="Int16")
    result.loc[is_irish, "birthplace"] = 414
    return result.reset_index(drop=True)


DEFAULT_BLOCKING_RULES: tuple[tuple[str, tuple[str, ...], int], ...] = (
    ("exact_names_yob2", ("first_norm", "last_norm", "sex", "birthplace"), 2),
    ("phonetic_names_yob3", ("first_nysiis", "last_nysiis", "sex", "birthplace"), 3),
    ("surname_first_initial_yob5", ("last_norm", "first_initial", "sex", "birthplace"), 5),
    ("name_initials_yob5", ("first_initial", "last_initial", "sex", "birthplace"), 5),
)


def generate_candidates(
    queries: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    rules: Sequence[tuple[str, tuple[str, ...], int]] = DEFAULT_BLOCKING_RULES,
) -> pd.DataFrame:
    """Generate a union of blocked pairs using vectorized hash joins.

    The returned columns are ``query_id``, ``target_id``, and ``rules``.  A
    target appearing through multiple rules is retained only once.
    """

    required = {"record_id", "birth_year"}
    for _, keys, _ in rules:
        required.update(keys)
    missing_query = required.difference(queries.columns)
    missing_target = required.difference(targets.columns)
    if missing_query or missing_target:
        raise KeyError(f"missing query columns={missing_query}; target columns={missing_target}")

    pieces: list[pd.DataFrame] = []
    for rule_name, keys, window in rules:
        query_columns = ["record_id", "birth_year", *keys]
        target_columns = ["record_id", "birth_year", *keys]
        query_block = queries[query_columns].dropna().copy()
        target_block = targets[target_columns].dropna().copy()
        if query_block.empty or target_block.empty:
            continue

        expanded = []
        for offset in range(-window, window + 1):
            shifted = query_block.copy()
            shifted["join_birth_year"] = shifted["birth_year"].astype("int32") + offset
            expanded.append(shifted)
        query_block = pd.concat(expanded, ignore_index=True)
        target_block = target_block.rename(
            columns={"record_id": "target_id", "birth_year": "join_birth_year"}
        )
        query_block = query_block.rename(columns={"record_id": "query_id"})
        pairs = query_block.merge(
            target_block,
            on=[*keys, "join_birth_year"],
            how="inner",
            sort=False,
        )[["query_id", "target_id"]]
        pairs["rule"] = rule_name
        pieces.append(pairs)

    if not pieces:
        return pd.DataFrame(columns=["query_id", "target_id", "rules"])
    pairs = pd.concat(pieces, ignore_index=True).drop_duplicates(["query_id", "target_id", "rule"])
    return (
        pairs.groupby(["query_id", "target_id"], sort=False)["rule"]
        .agg(lambda values: "|".join(sorted(values)))
        .rename("rules")
        .reset_index()
    )


def score_candidates(
    pairs: pd.DataFrame, queries: pd.DataFrame, targets: pd.DataFrame
) -> pd.DataFrame:
    """Attach interpretable name/age features and a simple ranking score."""

    if pairs.empty:
        return pairs.assign(
            first_jw=pd.Series(dtype=float),
            last_jw=pd.Series(dtype=float),
            birth_year_distance=pd.Series(dtype=float),
            score=pd.Series(dtype=float),
        )
    query_fields = queries[
        ["record_id", "first_norm", "last_norm", "birth_year"]
    ].rename(
        columns={
            "record_id": "query_id",
            "first_norm": "query_first",
            "last_norm": "query_last",
            "birth_year": "query_birth_year",
        }
    )
    target_fields = targets[
        ["record_id", "first_norm", "last_norm", "birth_year"]
    ].rename(
        columns={
            "record_id": "target_id",
            "first_norm": "target_first",
            "last_norm": "target_last",
            "birth_year": "target_birth_year",
        }
    )
    scored = pairs.merge(query_fields, on="query_id", how="left", validate="many_to_one")
    scored = scored.merge(target_fields, on="target_id", how="left", validate="many_to_one")
    scored["first_jw"] = [
        jaro_winkler_similarity(a, b)
        for a, b in zip(scored["query_first"], scored["target_first"])
    ]
    scored["last_jw"] = [
        jaro_winkler_similarity(a, b)
        for a, b in zip(scored["query_last"], scored["target_last"])
    ]
    scored["birth_year_distance"] = (
        scored["query_birth_year"].astype(float) - scored["target_birth_year"].astype(float)
    ).abs()
    age_similarity = 1 - scored["birth_year_distance"].clip(upper=5) / 5
    scored["score"] = 0.40 * scored["first_jw"] + 0.45 * scored["last_jw"] + 0.15 * age_similarity
    return scored


def top_candidates(scored: pd.DataFrame, count: int = 2) -> pd.DataFrame:
    """Return the highest-ranked candidates and the first/second score margin."""

    if scored.empty:
        return scored.copy()
    ranked = scored.sort_values(
        ["query_id", "score", "birth_year_distance", "target_id"],
        ascending=[True, False, True, True],
        kind="stable",
    ).copy()
    ranked["rank"] = ranked.groupby("query_id", sort=False).cumcount() + 1
    top = ranked[ranked["rank"].le(count)].copy()
    second = top[top["rank"].eq(2)].set_index("query_id")["score"]
    top.loc[top["rank"].eq(1), "margin"] = (
        top.loc[top["rank"].eq(1), "score"]
        - top.loc[top["rank"].eq(1), "query_id"].map(second)
    )
    return top


def summarize_candidate_counts(pairs: pd.DataFrame, query_ids: Iterable[object]) -> dict[str, float]:
    """Summarize candidate counts, including queries for which no pair was found."""

    query_ids = pd.Index(query_ids)
    counts = pairs.groupby("query_id").size().reindex(query_ids, fill_value=0)
    return {
        "queries": float(len(counts)),
        "queries_with_candidates": float(counts.gt(0).sum()),
        "coverage": float(counts.gt(0).mean()) if len(counts) else float("nan"),
        "candidate_pairs": float(counts.sum()),
        "mean_candidates": float(counts.mean()) if len(counts) else float("nan"),
        "median_candidates": float(counts.median()) if len(counts) else float("nan"),
        "p95_candidates": float(counts.quantile(0.95)) if len(counts) else float("nan"),
        "max_candidates": float(counts.max()) if len(counts) else float("nan"),
    }
