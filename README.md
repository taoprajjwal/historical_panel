# Historical County–Decade Panel

This repository contains the reproducible code and documentation for a U.S.
county-by-census-decade panel on fixed 1940 county boundaries. The panel covers
1850, 1860, 1870, 1880, 1900, 1910, 1920, 1930, and 1940; it does not create an
1890 observation.

The project has two interoperable builds:

1. a public replication-package backbone with population, socioeconomic, and
   connectivity measures; and
2. an optional full-count IPUMS USA extension with Irish-origin,
   intergenerational, household, occupational, language, migration, and other
   person-level measures aggregated to counties.

Raw microdata and generated panels are intentionally not committed. IPUMS users
must obtain their own authorized extract and comply with the applicable data-use
and citation requirements.

## Repository guide

- [`historical_panel/README.md`](historical_panel/README.md): detailed build and
  interpretation guide
- [`historical_panel/RESEARCH_MEMO.md`](historical_panel/RESEARCH_MEMO.md):
  plain-language explanation of a historical panel
- [`historical_panel/BUILD_BLUEPRINT.md`](historical_panel/BUILD_BLUEPRINT.md):
  geography, crosswalk, measures, provenance, and output design
- [`historical_panel/IPUMS_EXTENSION.md`](historical_panel/IPUMS_EXTENSION.md):
  IPUMS aggregation rules and additional analyses
- [`historical_panel/variable_catalog.csv`](historical_panel/variable_catalog.csv):
  replication-backbone variable dictionary
- [`historical_panel/ipums_variable_catalog.csv`](historical_panel/ipums_variable_catalog.csv):
  IPUMS extension variable dictionary
- [`historical_panel/source_ledger.csv`](historical_panel/source_ledger.csv):
  sources, access conditions, and checksums

## Setup

Python 3.11 or newer is recommended.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pytest
```

## Build the public replication backbone

The downloader retrieves the published Harvard Dataverse replication archive,
verifies it, and extracts only the allow-listed inputs. The build then downloads
public historical county geometry and creates the fixed-1940 crosswalk and
county-decade outputs.

```bash
python -m historical_panel.download_sources
python -m historical_panel.build_panel
```

## Add the IPUMS full-count extension

Create or download the authorized extract described in
[`historical_panel/IPUMS_EXTENSION.md`](historical_panel/IPUMS_EXTENSION.md),
then place these untracked files in the repository root:

- `usa_00002.dat.gz`
- `usa_00002.xml`

Run:

```bash
python -m historical_panel.aggregate_ipums
python -m historical_panel.build_ipums_catalog
python -m historical_panel.validate_ipums_output
```

The aggregator streams the compressed fixed-width file; do not decompress it.
Interrupted builds can be continued with `python -m
historical_panel.aggregate_ipums --resume`.

## Test

The test suite uses small synthetic fixtures and does not require the raw IPUMS
extract or generated panels.

```bash
pytest -q
```

## Untracked data products

All raw inputs, intermediate chunks, GIS caches, crosswalk binaries, and final
CSV/Parquet panels are ignored by Git. They are rebuilt under
`historical_panel/data/`. This prevents accidental redistribution of restricted
microdata and keeps the repository lightweight.

The completed reference build processed 664,367,751 person records across nine
full-count samples and produced 26,873 harmonized county-decade rows with 298
columns. See the detailed module README and validation documentation for
definitions and caveats; these dimensions are validation benchmarks, not
bundled data.
