# What a Historical Panel Means for Irish Migration and Music Diffusion

## The short version

A historical panel is a **time-lapse album of places**. Imagine laying out a page for every U.S. census decade. On each page, the same county—or, in this project, the same area drawn using 1940 county boundaries—has a card describing its population, Irish-born community, economy, and connections to other places. Reading across the pages shows how that place changed.

The panel is therefore a disciplined way to put facts from different dates into a comparable structure. It is not itself an argument that migration caused a musical change. It is the research infrastructure needed to describe sequences, compare places, identify plausible mechanisms, and later design a credible causal test.

## What is—and is not—being followed

Each panel row is a **place at a date**, such as Cook County's 1940 area observed in 1880. The panel follows geographic units, not particular people. An Irish migrant may arrive, move again, die, or return to Ireland between censuses; the panel will not trace that life. Instead, it records the changing number or share of Irish-born residents in each area.

That distinction matters. Repeated observations of Cook County form a geographic panel. A file containing different individuals sampled in every census is a set of repeated cross-sections. A person-level panel would require linking the same named people across dates. The first is sufficient for the main county-level question and avoids the severe selection and privacy problems of historical name linkage.

A folder of county files is also not automatically a panel. To become one, the files need:

- a stable meaning for the row key;
- comparable definitions or explicit warnings when definitions change;
- known geographic crosswalks when county borders change;
- a record of which values were observed and which were constructed; and
- tests that prevent duplicates, impossible shares, and invented dates.

## The clock and the map

The panel uses 1850, 1860, 1870, 1880, 1900, 1910, 1920, 1930, and 1940. The 1890 population schedules were largely destroyed, so there is no 1890 row. A gap is more honest than a number that merely looks complete.

County borders changed frequently. Comparing a small 1850 county with a larger county carrying the same name in 1940 can confuse border change with social change. This release intersects every historical county polygon with a fixed 1940 map and allocates source counts by the share of the source county's land area falling inside each 1940 county. This creates a stable `county_1940_id × census_year` key.

Area allocation is transparent, but it is an approximation: it treats residents and activity as evenly spread inside a source county. The crosswalk therefore retains the source and target identifiers, both overlap weights, overlap area, a weight-sum diagnostic, and a quality flag. It does not disguise harmonized estimates as original census observations. A separate 1940-to-2010 area crosswalk supports later modern music outcomes without changing the historical panel's unit.

## Five distinctions that prevent misleading interpretation

### Changing measures versus fixed attributes

Population, Irish-born share, urban population, manufacturing activity, and market potential can change from decade to decade. Land area in this release is fixed because every row uses the same 1940 county polygon. A river or coastline is physically fixed, though the economic usefulness of that feature can change. Repeating a fixed value down nine rows does not make it time-varying.

Rail access sits between these categories. It is measured from network snapshots in 1873, 1883, 1898, 1912, and 1921. A county's value can change as the network expands, but `first_observed_rail_snapshot_year` is only the earliest mapped snapshot in which a line intersects the county—not an exact construction date.

### Observation versus construction

The underlying county totals were transcribed from historical census tables. The published panel values are then area-harmonized. Shares and per-person quantities are calculated from those harmonized counts. Market potential is constructed from all other counties' populations divided by centroid-to-centroid distance. Rail access is constructed from line/polygon intersection. Each operation changes what the value means, so the catalog names the operation and units.

Interpolation is different again: it fills a value between observations according to an assumption about the path between them. This release does **not** interpolate socioeconomic variables and does not insert 1890. If annual population estimates are useful later, they should be a separate sensitivity product with an imputation flag.

### Missing versus zero

A zero says that a concept was observed and absent. Missing says that the source does not provide a defensibly comparable value. Irish-born counts are currently available from this replication source for 1870 onward, not for 1850 or 1860. Those early cells remain missing; they are not zero. The same principle applies to urbanization and economic variables whose historical table definitions do not span every decade.

### Balanced versus unbalanced panels

A balanced panel would have every county and every variable in all nine dates. Historical evidence rarely permits that. Some western counties did not yet exist; the census asked different questions; border harmonization may be weak; and some tables have not survived. This is an intentionally **unbalanced historical panel**: its key is consistent, but substantive coverage varies and is reported by variable and decade.

### Sequence versus cause

Suppose Irish-born share rose, rail access appeared, and music venues proliferated in the same county. That sequence is informative but not proof that migration caused the venues. Growing cities attracted migrants, performers, capital, and railroads simultaneously. Migrants also selected destinations, and counties influenced one another. A causal paper will need a research design that addresses selection, common trends, timing, spillovers, and measurement—not just a regression with many columns.

## What Martin Fiszbein's work contributes

The papers are useful because they solve different pieces of the same practical problem.

[*Frontier Culture*](https://economics.yale.edu/sites/default/files/fiszbein_paper.pdf) is a model for repeated county observations and explicit treatment of boundary change and between-census interpolation. The lesson here is not that every gap should be interpolated; it is that the unit, vintage, and construction of each observation must be stated.

[*Agricultural Diversity, Structural Change, and Long-run Development*](https://www.openicpsr.org/openicpsr/project/119556/version/V3/view) shows how population, farms, manufacturing, transport, market access, and later development can be assembled at county scale over a long period. Its openICPSR deposit is recorded in the source ledger as an audited extension source. This first release does not silently import a variable merely because a similarly named field exists there: definition and vintage must first be reconciled.

[*The Other Great Migration*](https://doi.org/10.7910/DVN/KLIPEM) provides the nearest structural template. Its public package supplies the county census table used here, rail snapshots, and a 2021 county country-radio example. The example is exported only to demonstrate the music-outcome interface. It is neither an Irish-music measure nor evidence that the proposed Irish outcome already exists. Some complete-count microdata used by the research remain restricted; the panel avoids depending on person names.

[*When Do Migrants Shape Culture?*](https://www.bu.edu/econ/files/2025/07/Migration_and_Culture-BF.pdf) broadens the interpretation of cultural change. Music diffusion might reflect persistence within migrant communities, gradual assimilation, or mixing between migrant and receiving traditions. Influence can pass through face-to-face contact, churches and societies, venues, newspapers, recordings, radio, touring networks, or migrants' positions inside cultural organizations. These are different mechanisms and may leave different geographic and temporal signatures.

## Reading the measures in ordinary language

`irish_born_count` is the estimated number of residents in the fixed 1940 county area who were born in Ireland under that census table's birthplace categories. `irish_born_share` divides that count by total population. It is the primary exposure because it is interpretable across places of very different size. Later work can add second-generation measures, but they must be separate: birthplace of parents answers a different question from birthplace of the resident.

There is deliberately no column called historical income. Farm value, manufacturing wages per manufacturing worker, and occupational or wealth measures capture different parts of material life. Calling any one of them “income” would imply comparability the sources do not provide. Nominal monetary measures also should not be compared across decades without a price adjustment appropriate to the question.

Trade access has multiple meanings. `rail_connected` records physical network presence. Water access, when supported by the source table, is another physical feature. `market_potential_persons_per_km` describes proximity to surrounding population: a remote rail county and a rail county next to major cities can have the same connection flag but very different potential markets. These measures belong side by side, not collapsed into one connectivity score.

## How music data will join

The music table is deliberately long rather than forcing an undecided outcome into the core panel. One row can describe a song, performer, venue, recording, radio station, society, newspaper, or coverage measure at a geography and date. It records the measure, unit, genre definition, source, and coverage caveat. Historical events can be joined to a county-decade by a documented dating rule; modern county outcomes can first use the 1940-to-2010 crosswalk.

Before interpreting an outcome as “Irish music diffusion,” the project should decide what makes the item Irish: performer origin, self-identification, repertoire, musical form, lyrics, institutional affiliation, audience, or an expert classification. Those are not interchangeable. Multiple definitions are often more informative than a single opaque genre label.

## What this first release can support

The release supports descriptive questions such as: Where did Irish-born concentration rise or fall? Did connected and high-market-potential counties show different timing? Were places with large Irish communities urban or industrial, and did that profile change? It also makes it possible to plot county histories while keeping observed absence, source absence, and boundary uncertainty separate.

It does not yet support a claim that Irish migration caused music diffusion. The next stage is to define and audit the music outcome, specify the relevant timing and mechanisms, and choose a design suited to that outcome. The panel's main contribution is that those later decisions can be made without rebuilding geography, provenance, and missingness from scratch.
