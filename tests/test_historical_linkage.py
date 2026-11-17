import pandas as pd

from historical_linkage import (
    generate_candidates,
    jaro_winkler_similarity,
    normalize_name,
    nysiis,
    split_given_name,
    split_passenger_name,
)


def test_name_cleaning_and_split():
    assert normalize_name("O'Neill, Jr.") == "ONEILLJR"
    assert split_passenger_name("Patrick Mc Kenna") == ("PATRICK", "", "MCKENNA")
    assert split_passenger_name("Mary Anne O Brien") == ("MARY", "A", "OBRIEN")
    assert split_passenger_name("Mrs Bridget Ryan") == ("BRIDGET", "", "RYAN")
    assert split_passenger_name("John Smith Jr.") == ("JOHN", "", "SMITH")
    assert split_passenger_name("Mrs Ryan") == ("RYAN", "", "")
    assert split_given_name("Mary J.") == ("MARY", "J")


def test_phonetic_and_jaro_winkler():
    assert nysiis("Macdonald") == nysiis("Mcdonald")
    assert jaro_winkler_similarity("Smith", "Smith") == 1.0
    assert jaro_winkler_similarity("Smith", "Smyth") > 0.85
    assert jaro_winkler_similarity("Smith", "Jones") < 0.6


def test_candidate_generation_respects_block_and_birth_window():
    query = pd.DataFrame(
        {
            "record_id": [1],
            "first_norm": ["PATRICK"],
            "last_norm": ["MCKENNA"],
            "first_nysiis": [nysiis("PATRICK")],
            "last_nysiis": [nysiis("MCKENNA")],
            "first_initial": ["P"],
            "last_initial": ["M"],
            "sex": pd.Series([1], dtype="Int8"),
            "birthplace": pd.Series([414], dtype="Int16"),
            "birth_year": pd.Series([1875], dtype="Int16"),
        }
    )
    target = pd.concat(
        [
            query.assign(record_id=10, birth_year=1877),
            query.assign(record_id=11, birth_year=1882),
            query.assign(record_id=12, birthplace=410),
        ],
        ignore_index=True,
    )
    pairs = generate_candidates(query, target)
    assert set(pairs["target_id"]) == {10}
