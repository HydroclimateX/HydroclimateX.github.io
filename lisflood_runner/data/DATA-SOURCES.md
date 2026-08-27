# LISFLOOD base data

- DEM: Copernicus DEM GLO-30 tiles N32E118 and N32E119, bilinearly projected to EPSG:32650 at 30 m. Source: https://registry.opendata.aws/copernicus-dem/
- Population: WorldPop R2025A constrained 2025 population counts for China, summed into the aligned 30 m grid. DOI: 10.5258/SOTON/WP00839. Source: https://hub.worldpop.org/geodata/summary?id=72922
- Extent: 665955.77, 3546538.43, 710895.77, 3571288.43 in EPSG:32650.
- Licences: Copernicus DEM licence and WorldPop CC BY 4.0; retain attribution when redistributing derived rasters.
- Processing: GDAL `gdalbuildvrt`, `gdalwarp`, and `gdal_translate` commands recorded in the implementation plan.
