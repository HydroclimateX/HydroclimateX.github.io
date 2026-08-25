# LISFLOOD Web runner

This one-shot container compiles the private model, runs the five fixed return
periods, and publishes static assets only after every check passes.

## Private server layout

Keep this outside the public repository at
`/opt/hydroclimatex-wasp/lisflood-private/`:

```text
source/                         modified source and CMakeLists.txt
model/
  ft.par                        validated parameter template
  dem.asc                       30 m EPSG:32650 DEM
  population.asc                aligned population-count grid
  ...                           SWMM and other model inputs
  windows-reference/
    5.max  10.max  20.max  50.max  100.max
```

The source check refuses an engine without `uniform_rules` and `inpFile`.
Production requires all five Windows references; each Linux result must have a
depth MAE no greater than 1 mm and wet-area difference no greater than 1%.
The runner also rejects mass-balance error above 3%. Generated rain files use
the header-first format of the current SWMM-coupled executable.

## Prepare open rasters

Provide a Qixia boundary buffered by 2 km, a Copernicus GLO-30 GeoTIFF and a
WorldPop 2025 population-count GeoTIFF:

```bash
scripts/prepare-lisflood-data.sh \
  qixia-buffer-2km.geojson copernicus-glo30.tif worldpop-2025.tif \
  /opt/hydroclimatex-wasp/lisflood-private/model
```

Review the cropped domain before generating the Windows reference results.

## Generate or deploy

```bash
docker compose --profile lisflood-tools run --rm lisflood-runner
sudo ./deploy-lisflood.sh
```

`LISFLOOD_REQUIRE_PARITY=0` is only for local pipeline development. Production
uses `1` and will not publish unverified results.
