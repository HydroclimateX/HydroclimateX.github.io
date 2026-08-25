# LISFLOOD Web runner

This one-shot container uses the official LISFLOOD-FP 8.0.3 CPU ACC engine,
runs five surface-flood scenarios, and publishes static assets only after every
check passes. Sewer networks and engineered drainage are not modelled.

## Private server layout

Keep model inputs outside the public repository at
`/opt/hydroclimatex-wasp/lisflood-private/model/`:

```text
ft.par          surface-model parameter template
dem.asc         30 m EPSG:32650 DEM
population.asc  aligned population-count grid
ft.bci          boundary conditions
depth.asc       initial depth
ft.n.asc        Manning roughness
ft.evap         evaporation series
```

The runner removes active `uniform_rules`, `inpFile`, FV1 and DG2 settings from
the generated scenario parameters, forces ACC, and retains surface infiltration,
evaporation, roughness and boundary inputs. It rejects mass-balance errors above
3% and publishes only when all five scenarios succeed.

## Prepare open rasters

```bash
scripts/prepare-lisflood-data.sh \
  qixia-buffer-2km.geojson copernicus-glo30.tif worldpop-2025.tif \
  /opt/hydroclimatex-wasp/lisflood-private/model
```

Review the prepared domain and surface-model parameters before deployment.

## Generate or deploy

```bash
docker compose --profile lisflood-tools run --rm lisflood-runner
sudo ./deploy-lisflood.sh
```

The first image build downloads the pinned official source archive from Zenodo;
Docker caches the compiled engine for later runs.
