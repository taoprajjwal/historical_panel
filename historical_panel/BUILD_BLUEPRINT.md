# Build Blueprint

## Data model

The release separates substantive observations from geography and documentation:

| Product | Key | Purpose |
|---|---|---|
| `county_decade_panel` | `county_1940_id + census_year` | Analysis-ready place-by-decade measures |
| `county_crosswalk` | `source_year + source_gisjoin + county_1940_id` | Reproducible historical boundary allocation |
| `county_1940_to_2010_crosswalk` | `county_1940_id + county_2010_id` | Optional bridge to modern outcomes |
| `variable_catalog` | `dataset + variable` | Meaning, source, units, years, construction, caveat |
| `source_ledger` | `source_id` | Citation, access, vintage, checksum, role |
| `music_outcome_template` | user-defined geography/date/measure | Modular cultural outcome interface |

The panel is wide because most analysis compares several county characteristics at once. Provenance, source exceptions, coverage, and crosswalk diagnostics remain separate so the analysis table does not become an unreadable collection of flags.

## Reproducible stages

1. **Acquire and verify.** `download_sources.py` downloads Harvard Dataverse file 6976313, verifies the pinned SHA-256, inventories the archive, and extracts only the county table, rail snapshots, documentation/code needed for audit, and the country-radio interface example.
2. **Standardize source fields.** `read_source_panel` reads only an allow-list from the 1,708-variable Stata table. `standardize_source` maps year-specific names into conservative common concepts. Blank territorial placeholders and duplicate keys are resolved by `clean_source_keys`; impossible source components are recorded and set missing.
3. **Acquire geometry.** Public decennial county layers are retrieved from the IPUMS/NHGIS ArcGIS service in chunks and cached as GeoParquet. Alaska, Hawaii, and Puerto Rico are excluded.
4. **Harmonize boundaries.** Each source-year polygon is intersected with the 1940 target polygons in an equal-area projection. Counts are multiplied by source-area weights. Names and identifiers are never used as a substitute for polygon overlap.
5. **Construct measures.** Shares and per-unit proxies use harmonized numerators and denominators. Market potential uses surrounding population and straight-line centroid distance. Rail access uses the latest network snapshot no later than the census date.
6. **Create modern bridge.** The same intersection method creates a separate 1940-to-2010 area crosswalk. It does not alter the historical unit.
7. **Validate and publish.** The build enforces key uniqueness, valid years and ranges, reconciles population before and after allocation, reports crosswalk quality and missingness, writes county examples, and exports Parquet plus compressed CSV.

## Current transformation rules

- Historical counts use `value × source_area_weight` and are summed within target county.
- A source polygon is `good` when its overlap weights sum from 0.98 to 1.02, `review` from 0.90 to 1.10, and `poor` otherwise.
- Irish-born fields are `pbirelan` in 1870/1880/1900, `pbwirel` in 1910/1920, and `pbweire + pbwulst` in 1930/1940. The 1850/1860 cells remain missing.
- The market-potential diagonal is excluded: a county's own population does not increase its value.
- Rail mapping is 1880→1873, 1900/1910→1898, 1920→1912, and 1930/1940→1921. No 1870 value is inferred from 1873.
- Monetary values remain nominal. No price deflator or synthetic income series is imposed.
- No socioeconomic series is interpolated, and no 1890 row exists.

## Adding a new historical source

Before merging a field, add its deposit and access conditions to `source_ledger.csv`, then add every candidate file to the source audit. Define the historical concept before choosing the similarly named source column. Document units, universe, table vintage, available dates, aggregation rule, missing codes, and whether the measure is observed, area-allocated, interpolated, or proxied.

If the source geography differs, create a source-to-1940 crosswalk rather than matching names. Counts can generally be allocated and summed. Rates, medians, prices, and categorical indicators need a concept-specific rule; they should not automatically use the count formula. Preserve the original source identifier and report overlap quality.

## Priority extensions

1. **Irish generations.** Audit parental-birthplace tables and add first- and second-generation counts separately. Never combine them with the Irish-born exposure without preserving both components.
2. **Ports and waterways.** Replace the legacy early-census water indicator with dated port records and documented distances to navigable rivers, coastlines, and ports. Keep geographic proximity separate from observed port activity.
3. **Economic comparability.** Reconcile openICPSR agricultural/manufacturing variables against the current source, choose genuine repeated concepts, and add price-adjusted sensitivity versions alongside—not in place of—nominal values.
4. **Music institutions.** Geocode Irish churches, societies, newspapers, venues, shops, labels, performers, recordings, touring routes, and radio coverage only after defining their date, unit, universe, and Irish classification.
5. **Alternative geography assumptions.** Where population rasters or lower-level enumeration data exist, create population-weighted crosswalks and compare them with area allocation. Retain both as sensitivity layers.
6. **Annual population layer.** If a design needs years between censuses, publish a separate interpolation table with method and imputation flags. Do not rewrite the observed decade panel.

## Joining a music outcome

A new music dataset should first be transformed to the columns in `music_outcome_template.csv`. Historical county measures may join directly when their geography is the 1940 harmonized unit. A source on contemporary historical borders needs its own crosswalk. A modern county source uses the 1940-to-2010 bridge, with explicit consideration of whether area weighting is sensible for that outcome.

The join rule should state whether an event belongs to the exact year, the preceding census stock, a decade bin, or an exposure window. Preserve event-level data when possible and aggregate only in a reproducible build step. If several defensible definitions of “Irish music” exist, publish separate measures instead of hiding the decision in a single indicator.

## Release acceptance checklist

- `county_1940_id + census_year` is unique and 1890 is absent.
- Every published panel field has one catalog row; every imported source has one ledger row.
- Crosswalk weights, poor/review counts, and source exceptions are reported.
- Counts are nonnegative; bounded shares are in range; subgroup counts do not exceed their denominators.
- Population loss from boundary allocation is quantified by decade.
- Missingness and coverage are exported by variable and decade.
- Rail and water results have documented hand checks.
- At least three county histories illustrate change without causal wording.
- The music interface can accept a new outcome without changing the core panel key.
