#!/usr/bin/env bash
# Prepare Copernicus GLO-30 and WorldPop 2025 rasters for the private model bundle.
set -euo pipefail

if [[ "$#" -ne 4 ]]; then
  printf 'Usage: %s qixia-buffer-2km.geojson copernicus-glo30.tif worldpop-2025.tif output-dir\n' "$0" >&2
  exit 2
fi

BOUNDARY="$1"
DEM_SOURCE="$2"
POPULATION_SOURCE="$3"
OUTPUT_DIR="$4"
[[ -f "$BOUNDARY" && -f "$DEM_SOURCE" && -f "$POPULATION_SOURCE" ]] || {
  printf 'All three source files must exist.\n' >&2
  exit 1
}
[[ "$OUTPUT_DIR" != "/" && "$OUTPUT_DIR" != "." ]] || {
  printf 'Refusing a broad output directory.\n' >&2
  exit 1
}

for command in gdal_translate gdalwarp; do
  command -v "$command" >/dev/null || { printf '%s is required.\n' "$command" >&2; exit 1; }
done

install -d -m 0755 "$OUTPUT_DIR"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

gdalwarp -overwrite -t_srs EPSG:32650 -tr 30 30 -tap \
  -cutline "$BOUNDARY" -crop_to_cutline -dstnodata -9999 \
  -r bilinear "$DEM_SOURCE" "$WORK_DIR/dem.tif"
gdal_translate -of AAIGrid -a_nodata -9999 "$WORK_DIR/dem.tif" "$OUTPUT_DIR/dem.asc"

# GDAL's sum resampler conserves population counts when moving to the model grid.
gdalwarp -overwrite -t_srs EPSG:32650 -tr 30 30 -tap \
  -cutline "$BOUNDARY" -crop_to_cutline -dstnodata -9999 \
  -r sum "$POPULATION_SOURCE" "$WORK_DIR/population.tif"
gdal_translate -of AAIGrid -a_nodata -9999 "$WORK_DIR/population.tif" "$OUTPUT_DIR/population.asc"

diff <(sed -n '1,5p' "$OUTPUT_DIR/dem.asc") <(sed -n '1,5p' "$OUTPUT_DIR/population.asc") >/dev/null || {
  printf 'Prepared DEM and population grids do not align.\n' >&2
  exit 1
}

printf '%s\n' \
  'DEM: Copernicus GLO-30, clipped to the supplied Qixia District + 2 km boundary.' \
  'Population: WorldPop 2025 100 m population counts, conservatively resampled.' \
  > "$OUTPUT_DIR/DATA-SOURCES.txt"
