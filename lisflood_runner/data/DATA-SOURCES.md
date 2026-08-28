# LISFLOOD base data

- DEM: Copernicus DEM GLO-30 tiles N32E118 and N32E119, bilinearly projected to EPSG:32650 at 30 m. Source: https://registry.opendata.aws/copernicus-dem/
- Population: WorldPop R2025A constrained 2025 population counts for China, summed into the aligned 30 m grid. DOI: 10.5258/SOTON/WP00839. Source: https://hub.worldpop.org/geodata/summary?id=72922
- Extent: 665955.77, 3546538.43, 710895.77, 3571288.43 in EPSG:32650.
- Licences: Copernicus DEM licence and WorldPop CC BY 4.0; retain attribution when redistributing derived rasters.
- Processing: GDAL `gdalbuildvrt`, `gdalwarp`, and `gdal_translate` commands recorded in the implementation plan.
- Access date: 2026-08-27.
- Copernicus DEM object URLs:
  - https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N32_00_E118_00_DEM/Copernicus_DSM_COG_10_N32_00_E118_00_DEM.tif
  - https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N32_00_E119_00_DEM/Copernicus_DSM_COG_10_N32_00_E119_00_DEM.tif
- WorldPop input URL: https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/2025/CHN/v1/100m/constrained/chn_pop_2025_CN_100m_R2025A_v1.tif
- Copernicus GLO-30 is a Digital Surface Model (DSM); buildings, infrastructure, and vegetation may be represented in its elevations. Readme and licence: https://copernicus-dem-30m.s3.amazonaws.com/readme.html and https://dataspace.copernicus.eu/sites/default/files/media/files/2025-06/copernicus_contributing_mission_data_access_v2_cop_dem_licenses.pdf
- WorldPop licensing: CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); redistribution of derived data must retain clear WorldPop/University of Southampton attribution and a link to the licence (https://www.worldpop.org/faq/).

## Reproduction

With the three downloaded objects saved as `dem_n32e118.tif`, `dem_n32e119.tif`, and `population_2025.tif`, these commands were run with GDAL 3.11.4:

```sh
gdalbuildvrt -overwrite dem.vrt dem_n32e118.tif dem_n32e119.tif
gdalwarp -overwrite -t_srs EPSG:32650 -te 665955.77 3546538.43 710895.77 3571288.43 -ts 1498 825 -r bilinear -dstnodata -9999 dem.vrt dem_warp.tif
gdalwarp -overwrite -t_srs EPSG:32650 -te 665955.77 3546538.43 710895.77 3571288.43 -ts 1498 825 -r sum -dstnodata 0 population_2025.tif population_warp.tif
gdal_translate -of AAIGrid -a_nodata -9999 dem_warp.tif dem.asc
gdal_translate -of AAIGrid -a_nodata -9999 population_warp.tif population.asc
gzip -n -9 -c dem.asc > dem.asc.gz
gzip -n -9 -c population.asc > population.asc.gz
```
