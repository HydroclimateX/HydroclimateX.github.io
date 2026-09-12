#!/usr/bin/env Rscript

# Regenerate the browser-ready validation data from WQM's bundled sample.
# Usage: Rscript scripts/generate-wqm-demo.R [output.json]

if (!requireNamespace("WQM", quietly = TRUE)) {
  stop("WQM 0.1.4 is required")
}
if (!requireNamespace("WaveletComp", quietly = TRUE)) {
  stop("WaveletComp is required")
}
if (!requireNamespace("jsonlite", quietly = TRUE)) {
  stop("jsonlite is required")
}
stopifnot(packageVersion("WQM") == "0.1.4")

arguments <- commandArgs(trailingOnly = TRUE)
output <- if (length(arguments)) arguments[[1]] else file.path("interactive-apps", "wqm", "demo.json")

data("sample", package = "WQM")
calibration <- seq_len(floor(nrow(sample[[1]]) / 2))

# WQM 0.1.4 refers to J during its ensemble-size check without binding it.
# Derive the value from the same CWT used by bc_cwt and expose only that scalar.
initial_wavelet <- WaveletComp::WaveletTransform(
  x = sample[[1]]$obs[-calibration],
  dt = 1,
  dj = 1
)
J <- ncol(t(initial_wavelet$Wave))
assign("J", J, envir = .GlobalEnv)

set.seed(2021)
corrected <- WQM::bc_cwt(
  data = sample,
  subset = calibration,
  variable = "prep",
  theta = 0.1,
  QM = "QDM",
  number_sim = 5,
  wavelet = "morlet",
  dt = 1,
  dj = 1,
  method = "M2",
  block = 3,
  seed = 2021,
  PR.cal = FALSE,
  do.plot = FALSE
)

as_nonnegative <- function(values) {
  values <- as.numeric(values)
  values[!is.finite(values)] <- 0
  pmax(values, 0)
}

stations <- lapply(seq_along(corrected), function(index) {
  validation <- corrected[[index]]$val
  list(
    id = as.character(validation$Station[[1]]),
    label = paste("Station", validation$Station[[1]]),
    validation = list(
      date = format(as.POSIXct(validation$Date, origin = "1970-01-01", tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
      observed = as_nonnegative(validation$obs),
      raw = as_nonnegative(validation$mod),
      corrected = as_nonnegative(validation$bcc),
      r1 = as_nonnegative(validation$r1),
      r2 = as_nonnegative(validation$r2),
      r3 = as_nonnegative(validation$r3),
      r4 = as_nonnegative(validation$r4),
      r5 = as_nonnegative(validation$r5)
    )
  )
})

payload <- list(
  schemaVersion = 1L,
  package = list(name = "WQM", version = as.character(packageVersion("WQM"))),
  parameters = list(
    method = "QDM",
    wavelet = "morlet",
    dt = 1,
    dj = 1,
    levels = J,
    precipitationThreshold = 0.1,
    phaseMethod = "M2",
    block = 3,
    ensembleMembers = 5L,
    seed = 2021L,
    calibrationFraction = 0.5
  ),
  stations = stations
)

dir.create(dirname(output), recursive = TRUE, showWarnings = FALSE)
jsonlite::write_json(payload, output, auto_unbox = TRUE, digits = NA, pretty = FALSE)
message("Wrote ", output)
