import gzip
from pathlib import Path

import pandas as pd

from ipums_convert import (
    irish_candidate_index,
    parse_fixed_width_block,
    read_ddi,
    validate_ddi_for_data,
)


TINY_DDI = """<?xml version="1.0" encoding="UTF-8"?>
<codeBook xmlns="ddi:codebook:2_5" ID="tiny">
  <fileDscr ID="ExtractData"><fileTxt>
    <fileName>tiny.dat</fileName><fileType charset="ISO-8859-1">ISO-8859-1</fileType>
  </fileTxt></fileDscr>
  <dataDscr>
    <var ID="YEAR" name="YEAR" dcml="0"><location StartPos="1" EndPos="4" width="4"/><labl>Year</labl><varFormat type="numeric"/></var>
    <var ID="SAMPLE" name="SAMPLE" dcml="0"><location StartPos="5" EndPos="10" width="6"/><labl>Sample</labl><varFormat type="numeric"/></var>
    <var ID="NAME" name="NAME" dcml="0"><location StartPos="11" EndPos="15" width="5"/><labl>Name</labl><varFormat type="character"/></var>
  </dataDscr>
</codeBook>
"""


def test_ddi_and_strided_fixed_width_parser(tmp_path: Path):
    ddi_path = tmp_path / "tiny.xml"
    data_path = tmp_path / "tiny.dat.gz"
    ddi_path.write_text(TINY_DDI)
    with gzip.open(data_path, "wb") as handle:
        handle.write(b"1850185001MURPH\n1860186003BRIEN\n")

    ddi = read_ddi(ddi_path)
    assert ddi.record_width == 15
    assert [variable.name for variable in ddi.variables] == ["YEAR", "SAMPLE", "NAME"]
    validate_ddi_for_data(ddi, data_path)
    with gzip.open(data_path, "rb") as handle:
        parsed = parse_fixed_width_block(handle.read(), ddi=ddi, fields=("YEAR", "SAMPLE", "NAME"))
    assert parsed.to_dict("records") == [
        {"year": 1850, "sample": 185001, "name": "MURPH"},
        {"year": 1860, "sample": 186003, "name": "BRIEN"},
    ]


def test_irish_candidate_index_filters_and_builds_fallback_id():
    people = pd.DataFrame(
        {
            "year": [1850, 1850],
            "sample": [185001, 185001],
            "serial": [10, 11],
            "pernum": [2, 1],
            "histid": ["", "US-ID"],
            "namefrst": ["Mary J", "John"],
            "namelast": ["O'Brien", "Smith"],
            "sex": [2, 1],
            "age": [30, 40],
            "birthyr": [1820, 1810],
            "bpl": [414, 99],
            "bpld": [41400, 9900],
            "nativity": [2, 1],
            "citizen": [3, 0],
            "yrimmig": [1845, 0],
            "relate": [3, 1],
            "related": [301, 101],
            "marst": [1, 1],
        }
    )
    candidates = irish_candidate_index(people)
    assert len(candidates) == 1
    row = candidates.iloc[0]
    assert row["record_id"] == "185001:10:2"
    assert row["first_norm"] == "MARY"
    assert row["middle_initial"] == "J"
    assert row["last_norm"] == "OBRIEN"
    assert row["birth_year"] == 1820
    assert row["birth_decade"] == 1820
