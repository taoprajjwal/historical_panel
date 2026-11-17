# Historical County–Decade Panel

This module builds a conterminous-U.S. county panel for 1850–1940 on fixed 1940 county boundaries. It excludes 1890, retains genuine missing values, and keeps later music outcomes modular.

Read [RESEARCH_MEMO.md](RESEARCH_MEMO.md) for a non-technical explanation, [BUILD_BLUEPRINT.md](BUILD_BLUEPRINT.md) for the pipeline and extension design, [IPUMS_EXTENSION.md](IPUMS_EXTENSION.md) for the full-count aggregation rules, [variable_catalog.csv](variable_catalog.csv) for the replication-based release fields, and [ipums_variable_catalog.csv](ipums_variable_catalog.csv) for the IPUMS extension fields.

## Reproduce the build

The first command downloads the 1.34 GB Harvard Dataverse archive, verifies its SHA-256, extracts only the allow-listed inputs, and removes the ZIP. The extracted county table is about 158 MB.

```bash
python -m historical_panel.download_sources
python -m historical_panel.build_panel
pytest -q tests/test_historical_panel.py
```

Required Python packages are in `requirements.txt`. The build also downloads public decennial county geometry from the IPUMS/NHGIS ArcGIS service and caches it as GeoParquet.

### Add the IPUMS full-count extension

The replacement extract is `usa_00002.dat.gz`, with matching DDI metadata in
`usa_00002.xml`. The DDI defines a 358-character fixed-width record with 108
physical columns: 92 of the 93 requested variables plus 16 detailed companion
columns. `SLREC` is the only requested selection absent; `SLWT` is present and
is the field needed to weight 1940 sample-line measures.

`ipums_extract_audit.csv` lists every physical field, label, fixed-width
position, requested-selection status, and whether the initial county
aggregation consumes it. `ipums_extract_audit.json` provides the corresponding
schema summary.

Do not decompress the 27.3 GiB archive. The aggregator streams the gzip once,
writes resumable county-level partials with a bounded process pool, joins
historical IPUMS county codes to NHGIS `GISJOIN` identifiers, and then applies
the existing source-to-1940 crosswalk. IPUMS `COUNTYNHG` is the direct join for
1850–1930 when populated; the 1900 full count has `COUNTYNHG == 0` and uses the
documented `STATEICP + COUNTYICP` fallback, which is also used in 1940.

```bash
python -m historical_panel.aggregate_ipums
python -m historical_panel.build_ipums_catalog
python -m historical_panel.validate_ipums_output
```

If interrupted, rerun with `--resume`. The extension produces:

- `ipums_source_county_decade.parquet`: observed IPUMS aggregates on each
  census year's original county geography, unique by year and `GISJOIN`;
- `ipums_source_key_decade.parquet`: the more detailed pre-consolidation audit
  table retaining the original IPUMS geography keys;
- `ipums_county_decade_1940.parquet`, `.csv.gz`, and an inspection-ready
  uncompressed `.csv`: additive IPUMS measures allocated to the fixed 1940
  geography; and
- `ipums_variable_coverage.csv`: nonzero county counts and national sums by
  variable and decade;
- `ipums_unmatched_source_geographies.csv`: documented non-county or otherwise
  unmatched source keys that are not forced onto a polygon; and
- `ipums_geography_allocation_audit.csv`: source polygon overlap-weight sums
  and the implied unallocated population and Irish-born counts; and
- `ipums_validation_report.json`: machine-readable checksums, dimensions,
  sample coverage, uniqueness, subgroup bounds, unmatched share, and
  crosswalk-allocation checks.

The completed extract contains 664,367,751 records in the nine full-count
samples. The final extension has 298 columns, 23,527 observed source
county-decades, and 26,873 rows after harmonization to 1940 geography. Forty-
eight source-key rows (0.0251% of source population) are explicitly unmatched.
No matched source county is missing from the polygon crosswalk; small early-
decade allocation differences arise from shoreline and overlay slivers and are
reported in the allocation audit.

The extension retains separate numerators and observed-universe denominators.
This is essential because parental birthplace, immigration, schooling,
language, income, and migration questions were not asked of the same universe
in every census. In 1940, applicable sample-line measures use `SLWT`; universal
person measures use `PERWT`, and one-row-per-household measures use `HHWT`.

## Outputs

`data/processed/` contains:

- `county_decade_panel.parquet` and `.csv.gz`: analysis table keyed by `county_1940_id + census_year`;
- `county_crosswalk.parquet`: historical source polygon to 1940 county overlap weights and diagnostics;
- `county_1940_to_2010_crosswalk.parquet`: optional bridge for modern county outcomes;
- `variable_coverage.csv`: nonmissing counts and shares by variable and decade;
- `validation_report.json`: key, range, crosswalk-quality, and population-reconciliation checks;
- `county_history_examples.csv`: readable histories for Boston/Suffolk, New York, and Chicago/Cook when those labels are present;
- `source_quality_flags.csv`: source count anomalies set missing rather than propagated;
- `country_music_radio_example_2021.csv`: interface example from Fiszbein et al., not an Irish outcome.

The hand-authored `access_spot_checks.csv` records rail and water sanity checks for a small set of known counties.

The hand-authored `music_outcome_template.csv`, `variable_catalog.csv`, and `source_ledger.csv` are stable documentation products and are not overwritten by the build. `replication_inventory.csv` inventories all 1,428 files in the Dataverse archive, classifies tables and scripts, and records the release-1 import decision; the downloader regenerates it.

## What IPUMS can add beyond the replication package

The replication package provides a valuable county-level backbone, including population, selected demographic and economic measures, transport variables, and Irish-born counts beginning in 1870. Because these are already aggregated county tables, however, they cannot be used to reconstruct many distinctions among individual migrants, their children, households, arrival cohorts, or socioeconomic outcomes.

A revised public IPUMS full-count extract can extend the panel in two ways. First, it can fill important gaps in the county aggregates, most notably Irish birthplace measures in 1850 and 1860. Second, it can generate new county-by-decade measures by aggregating person- and household-level characteristics. The principal migration additions are:

| Constructed measure                      | IPUMS ingredients                    | Availability              |
| ---------------------------------------- | ------------------------------------ | ------------------------- |
| Irish-born count and share               | `BPL`                                | 1850–1940                 |
| Second-generation Irish count and share  | `MBPL`, `FBPL`, `BPL`                | Primarily 1880–1940       |
| One versus two Irish-born parents        | `MBPL`, `FBPL`                       | Primarily 1880–1940       |
| Irish arrival cohorts                    | `YRIMMIG`, `YRSUSA1`, `YRSUSA2`      | 1900–1930                 |
| Recent versus established Irish migrants | Arrival year relative to census year | 1900–1930                 |
| Naturalization share                     | `CITIZEN`                            | Selected years, 1870–1940 |
| Irish mother-tongue population           | `MTONGUE`                            | 1920–1940                 |
| English-language assimilation            | `SPEAKENG`, `LANGUAGE`               | 1900–1930/1910            |

IPUMS would also permit new measures that the replication aggregates cannot recover:

- Irish and non-Irish literacy, school attendance, education, labor-force participation, employment, occupation, industry, and occupational-status gaps;
- music-related occupational concentration, including musicians, music teachers, performers, and related occupations where the historical codes permit identification;
- intermarriage, mixed-origin households, children of Irish-born parents, multigenerational households, and other household-contact measures;
- homeownership, mortgages, rent, housing value, historical property wealth, 1940 wages, and non-wage income by Irish background;
- 1930 radio ownership among Irish and non-Irish households;
- Irish settlement by urban status, metropolitan status, and city size; and
- Irish-specific internal migration and prior-county flows for 1935–1940.

These additions support analyses of generational persistence, assimilation, occupational pathways, household transmission, socioeconomic selection, radio access, and internal mobility. They also allow comparisons between Irish and non-Irish people within the same county and decade, rather than relying exclusively on comparisons between counties.

The IPUMS extension is kept as a separate join-ready table rather than silently replacing the released 60-column replication-based panel. The new `usa_00002` extract contains the required current-county and socioeconomic fields, and `aggregate_ipums.py` performs the county aggregation. Variable availability and universes differ across decades, so unavailable values must remain missing rather than be interpreted as zero or interpolated automatically.

## Important interpretation rules

- Counts in the finished panel are area-allocated estimates, not untouched county-table values.
- The stable geography is the 1940 polygon; `land_area_sq_miles_1940` therefore does not vary over time.
- Missing means unavailable or not comparable. It never means zero.
- Irish-born counts in the current replication table begin in 1870.
- Monetary values are nominal and concept-specific. They are not a synthetic income series.
- Rail connection comes from the latest available prior snapshot and does not identify an exact construction year.
- Market potential excludes the county's own population and is not the same as physical transport access.
- The panel describes change. It does not by itself identify a causal effect of migration.

## Provenance

The source ledger records URLs, archive identifiers, checksums, access conditions, and the role of each source. The deposited source table contains a small number of blank territorial records and six duplicated county-year GISJOINs. `clean_source_keys` resolves these deterministically by preferring populated county rows and Haines FIPS values consistent with the NHGIS key. The transformation is tested and documented instead of hidden in manual edits.
