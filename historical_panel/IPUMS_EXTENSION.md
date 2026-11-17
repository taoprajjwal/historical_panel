# IPUMS Full-Count Extension

## Scope

The `usa_00002` extract adds public IPUMS USA person and household records to
the replication-based county panel. It contains the 1850, 1860, 1870, 1880,
1900, 1910, 1920, 1930, and 1940 full-count databases. There is no 1890
observation.

The matching DDI describes 108 physical fixed-width columns in a 358-character
record. Ninety-two of the 93 requested selections are present, along with 16
detailed companion columns. `SLREC` is absent. This does not prevent the
county aggregation: a positive `SLWT` identifies and weights the 1940
sample-line observations used for applicable questions.

The initial county aggregation reads 89 of the 92 present requested fields.
The three requested fields it deliberately does not aggregate are
`VERSIONHIST`, `HISTID`, and `SURSIM`; these are record-version or linkage
identifiers rather than county socioeconomic measures. Five detailed companion
fields are also read where their extra coding is substantively useful. The
complete field-level decision is recorded in `ipums_extract_audit.csv`.

The raw extract is governed by the IPUMS USA data-use terms. Do not place the
27.3 GiB person-level archive in a shared public folder. The derived county
aggregates are the intended collaboration product, subject to the applicable
IPUMS citation and redistribution requirements.

## Processing model

`aggregate_ipums.py` reads the compressed fixed-width file directly. It never
creates an uncompressed person-level copy. Each block is reduced to additive
county numerators and denominators and written as a resumable Parquet partial.

The processing sequence is:

```text
IPUMS person and household records
        ↓
historical county totals keyed by YEAR + STATEICP + COUNTYICP
        ↓
official source geography join:
COUNTYNHG → GISJOIN (1850–1930 when available)
STATEICP + COUNTYICP fallback (1900 full count and 1940)
        ↓
one row per census year + historical GISJOIN
        ↓
historical-county-to-1940 polygon crosswalk
        ↓
join-ready county_1940_id × census_year extension
```

The first output preserves observed census-year county geography. The second
uses the same area-overlap method as the replication-based panel. Consequently,
the source-geography totals are microdata aggregates, while pre-1940 values on
the final geography are area-allocated estimates.

`COUNTYNHG` is a seven-digit identifier and must not be narrowed to a 16-bit
integer. Codes of `9999999` and other documented non-county cases remain in the
source-key audit with `source_geography_matched == false`; they are never
assigned to a county by guesswork. Alternative IPUMS source keys that resolve
to the same official year-specific `GISJOIN` are summed before crosswalking.

## Weighting

- `PERWT` weights universal person measures such as population, birthplace,
  age, sex, and harmonized occupation.
- `HHWT` weights one-record-per-household measures, identified using
  `PERNUM == 1` and household rather than group-quarter status.
- `SLWT` weights 1940 sample-line measures such as parental birthplace,
  education, income, and other applicable questions.
- In years where a variable is universal, the ordinary person weight is used.

The output retains an observed-universe denominator beside each rate
numerator. A zero numerator with a positive denominator means that the concept
was observed but no qualifying cases were recorded. A zero denominator means
that a rate should not be constructed for that county-year.

## Principal constructed measures

### Irish origins

- Irish-born residents: `BPL == 414`.
- Second-generation Irish: U.S.-born residents with an Irish-born mother or
  father.
- One versus two Irish-born parents.
- Combined Irish-origin population, estimated on the sample-line universe in
  1940 so that the parental-birthplace component remains representative.
- Recent arrivals within ten years of the census and longer-established Irish
  immigrants, where `YRIMMIG` is observed.
- Naturalization among Irish-born residents with observed citizenship.
- Strict and broad Irish/Gaelic mother-tongue and spoken-language measures
  using the detailed IPUMS language codes.
- English-speaking status among Irish-born residents.

### Demography, education, and work

For the total population, Irish-born population, second generation, and the
combined Irish-origin group, the extension constructs additive numerators and
denominators for:

- urban, metropolitan, and identified-city residence;
- sex, children, working-age adults, older adults, and marriage;
- literacy, school attendance, and high-school-or-higher education;
- labor-force participation, employment, and unemployment;
- self-employment and wage work;
- agricultural and entertainment-industry employment;
- musicians and music teachers (`OCC1950 == 057`);
- occupational income, socioeconomic, prestige, earnings, education, and
  status scores;
- weeks and hours worked; and
- wage income, non-wage income status, and historical property values where
  available.

### Household and circulation measures

- Household count, homeownership, mortgage status, farm status, housing value,
  and 1930 rent.
- 1930 radio ownership overall and among households whose reference person is
  Irish-born or second generation.
- 1935–1940 migration status overall and among Irish-born residents.

## Deferred constructions

The first aggregation does not yet claim to measure intermarriage. A defensible
intermarriage measure requires resolving `SPLOC` within complete households and
handling spouses split across processing blocks. Similarly, a full 1935–1940
county origin-destination matrix should be released as a separate long table,
not folded into the wide county panel.

These fields remain present in the raw extract and can be added without
requesting another IPUMS download.

## Validation requirements

Before treating the extension as complete:

1. The parallel-download manifest must report every range complete.
2. The gzip stream must reach its CRC-checked end during aggregation.
3. The DDI filename and 358-character record width must match the archive.
4. Only the configured full-count sample identifiers may enter the aggregates.
5. Every populated `COUNTYNHG` must match the same-year NHGIS `GISJOIN`, while
   the 1900/1940 ICPSR fallback and every unmatched source key are audited.
6. Source-geography population totals must be reconciled with published census
   totals.
7. Crosswalk allocation loss must be reported by decade.
8. Subgroup counts must not exceed their corresponding observed-universe
   denominators.
9. Variables outside their historical universe must remain unavailable rather
   than be interpreted as substantive zeros.

## Completed build facts

- Raw archive: 29,295,278,495 compressed bytes; gzip CRC validated.
- Raw SHA-256: `03a66ca14aa4c24bced9e4b585ca08a2c9b9ab0fbe6e2db3a289c3464300650b`.
- DDI SHA-256: `42c7cad4c8f9e8bf675f2bc34a10b5f67d40bc05762431c1dd4a819a6f633bbf`.
- Records aggregated: 664,367,751 in 2,658 resumable chunks.
- Consolidated source county-decades: 23,527.
- Harmonized county-decades: 26,873.
- Final harmonized columns: 298.
- Unmatched source-key rows: 48, representing 0.0251% of source population.
