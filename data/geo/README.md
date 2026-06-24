# Geographic crosswalk — ZIP ↔ county / city

`zip_county_crosswalk.json.gz` maps every US ZIP to its county (FIPS + name) and primary city, so a
search scoped to a **county** (e.g. "Sacramento County, CA") or a **city** can enumerate its complete,
authoritative ZIP set — instead of guessing from whatever ZIPs happen to surface in a single capped
API scan. Consumed by [`dashboard/geo.py`](../../dashboard/geo.py); drives the county/city deep-scan
fan-out in `dashboard/search_service.py`.

## Schema (gzipped JSON)
```
counties     { "<5-digit FIPS>": { "name", "state", "zips": [...] } }   # residential ZIPs only
county_index { "<state_lower>|<county_lower>": "<FIPS>" }               # name → FIPS
city_index   { "<state_lower>|<city_lower>": [zips...] }
zip_meta     { "<zip>": { "city", "state", "county_fips" } }
```
PO-box / government / placeholder ZIPs (clusters that share one centroid — e.g. Sacramento's 942xx
range) are excluded so the fan-out never burns API calls on ZIPs with no for-sale inventory.

## Source & license
Built from the **GeoNames US postal-code dump** (<https://download.geonames.org/export/zip/US.zip>).

> Geographic data © [GeoNames](https://www.geonames.org/), licensed under
> [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/).

This attribution also rides inside the data file (`attribution` field). CC-BY permits commercial use
and redistribution with attribution — satisfied here and in the file.

## Regenerate
```
python data/geo/build_crosswalk.py            # downloads US.zip, writes zip_county_crosswalk.json.gz
python data/geo/build_crosswalk.py US.txt     # or pass a pre-downloaded GeoNames US.txt
```
GeoNames refreshes periodically; new ZIPs are rare, so regeneration is occasional.
