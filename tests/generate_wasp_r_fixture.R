#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(WASP)
  library(waveslim)
  library(jsonlite)
  library(openssl)
})

root <- normalizePath(getwd(), mustWork = TRUE)
csv_path <- file.path(root, "examples", "wasp_demo.csv")
out_path <- file.path(root, "tests", "fixtures", "wasp_r_parity.json")

hash_public <- function(x) {
  unclass(as.character(openssl::sha256(
    charToRaw(paste(sprintf("%.4f", round(as.numeric(x), 4)), collapse = ","))
  )))
}

round_metric <- function(x, digits) {
  if (is.finite(x)) round(x, digits) else NULL
}

metrics <- function(obs, pred) {
  residuals <- obs - pred
  mse <- mean(residuals^2)
  rmse <- sqrt(mse)
  mae <- mean(abs(residuals))
  ss_res <- sum(residuals^2)
  ss_tot <- sum((obs - mean(obs))^2)
  nse <- 1 - ss_res / ss_tot
  obs_sd <- sqrt(mean((obs - mean(obs))^2))
  pred_sd <- sqrt(mean((pred - mean(pred))^2))
  correlation <- cor(obs, pred)
  alpha <- sd(pred) / sd(obs)
  beta <- mean(pred) / mean(obs)
  kge <- 1 - sqrt((correlation - 1)^2 + (alpha - 1)^2 + (beta - 1)^2)
  threshold <- quantile(obs, 0.9, names = FALSE)
  obs_event <- obs >= threshold
  pred_event <- pred >= threshold
  hits <- sum(obs_event & pred_event)
  false_alarms <- sum(!obs_event & pred_event)
  misses <- sum(obs_event & !pred_event)
  pod <- hits / (hits + misses)
  far <- false_alarms / (hits + false_alarms)
  csi <- hits / (hits + misses + false_alarms)
  list(
    mse = round_metric(mse, 6), rmse = round_metric(rmse, 4),
    mae = round_metric(mae, 4), nse = round_metric(nse, 4),
    kge = round_metric(kge, 4), correlation = round_metric(correlation, 4),
    pod = round_metric(pod, 4), far = round_metric(far, 4),
    csi = round_metric(csi, 4), n_samples = length(obs)
  )
}

hashes_for <- function(x) {
  list(length = length(x), sha256 = as.character(hash_public(x)))
}

data <- read.csv(csv_path, check.names = FALSE)
cal <- data[1:600, , drop = FALSE]
val <- data[601:1200, , drop = FALSE]
wavelets <- c(db1 = "haar", db2 = "d4", db4 = "d8", db8 = "d16")
levels <- c(db1 = 9L, db2 = 7L, db4 = 6L, db8 = 5L)
cases <- list()

for (public_wavelet in names(wavelets)) {
  r_wavelet <- wavelets[[public_wavelet]]
  J <- levels[[public_wavelet]]
  fit <- dwt.vt(
    list(x = cal[[1]], dp = cal[, -1, drop = FALSE]),
    wf = r_wavelet, J = J, method = "dwt", pad = "zero",
    boundary = "periodic", cov.opt = "auto", verbose = FALSE
  )
  validation <- dwt.vt.val(
    list(x = val[[1]], dp = val[, -1, drop = FALSE]),
    J = J, dwt = fit, verbose = FALSE
  )
  factors <- apply(fit$S, 2, function(s) as.numeric(s / sqrt(sum(s^2))))
  cal_x <- as.matrix(fit$dp.n)
  val_x <- as.matrix(validation$dp.n)
  cal_y <- cal[[1]]
  val_y <- val[[1]]
  cal_wasp <- as.numeric(cbind(1, cal_x) %*% lm.fit(cbind(1, cal_x), cal_y)$coefficients)
  val_wasp <- as.numeric(cbind(1, val_x) %*% lm.fit(cbind(1, cal_x), cal_y)$coefficients)
  raw_x_cal <- as.matrix(cal[, -1, drop = FALSE])
  raw_x_val <- as.matrix(val[, -1, drop = FALSE])
  raw_fit <- lm.fit(cbind(1, raw_x_cal), cal_y)
  cal_baseline <- as.numeric(cbind(1, raw_x_cal) %*% raw_fit$coefficients)
  val_baseline <- as.numeric(cbind(1, raw_x_val) %*% raw_fit$coefficients)
  predictor_names <- names(cal)[-1]
  transformed <- setNames(lapply(seq_along(predictor_names), function(i) {
    list(calibration = hashes_for(cal_x[, i]), validation = hashes_for(val_x[, i]))
  }), predictor_names)
  case <- list(
    python_wavelet = public_wavelet,
    r_wavelet = r_wavelet,
    level = J,
    modulation_factors = setNames(lapply(seq_len(ncol(factors)), function(i) {
      as.numeric(factors[, i])
    }), predictor_names),
    public_modulation_factors = setNames(lapply(seq_len(ncol(factors)), function(i) {
      round(as.numeric(factors[, i]), 4)
    }), predictor_names),
    transformed_predictors = transformed,
    predictions = list(
      calibration = list(
        observed = hashes_for(cal_y),
        wasp = hashes_for(cal_wasp),
        baseline = hashes_for(cal_baseline)
      ),
      validation = list(
        observed = hashes_for(val_y),
        wasp = hashes_for(val_wasp),
        baseline = hashes_for(val_baseline)
      )
    ),
    metrics = list(
      calibration_wasp = metrics(cal_y, cal_wasp),
      calibration_baseline = metrics(cal_y, cal_baseline),
      wasp = metrics(val_y, val_wasp),
      baseline = metrics(val_y, val_baseline)
    )
  )
  cases[[public_wavelet]] <- case
}

fixture <- list(
  source = list(
    dataset = "examples/wasp_demo.csv",
    dataset_sha256 = unclass(as.character(openssl::sha256(
      readBin(csv_path, what = "raw", n = file.info(csv_path)$size)
    ))),
    wasp_commit = "5096903",
    wasp_version = as.character(packageVersion("WASP")),
    waveslim_version = as.character(packageVersion("waveslim")),
    r_version = R.version.string
  ),
  protocol = list(
    target_column = "SPI12",
    predictor_columns = names(data)[-1],
    calibration_rows = "1:600",
    validation_rows = "601:1200",
    test_size = 0.5,
    model = "linear",
    output_precision = 4,
    dwt_vt = "dwt.vt(data, wf, J, method='dwt', pad='zero', boundary='periodic', cov.opt='auto', verbose=FALSE)",
    dwt_vt_val = "dwt.vt.val(data, J, dwt, verbose=FALSE)",
    hash_encoding = "UTF-8 comma-separated sprintf('%.4f', round(value, 4))"
  ),
  wavelet_mapping = as.list(wavelets),
  auto_levels = as.list(levels),
  cases = cases
)

write(toJSON(fixture, auto_unbox = TRUE, pretty = TRUE, digits = 17, null = "null"), out_path)
