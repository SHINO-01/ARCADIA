"""ARCADIA agronomic world model.

A daily, spatially explicit field simulation, vectorised over bed sections:

  climate   - weather-generator parameters fitted to the farm's own daily history (Open-Meteo ERA5)
  weather   - Richardson-type stochastic generator (Markov rain occurrence, gamma amounts, AR(1)
              multivariate temperature/radiation residuals) + FAO-56 Penman-Monteith ET0
  soil      - Gaussian-random-field soil and relief maps around SoilGrids means, Saxton & Rawls
              (2006) hydraulics, D8 terrain wetness index, SCS curve numbers
  crops     - crop parameter sets (FAO-56 Kc/p/Ky, thermal time, RUE, harvest index, stresses)
  model     - daily soil-water balance, phenology, canopy, biomass, nitrogen, weeds, disease,
              frost/heat/waterlogging/salinity stress, harvest losses
  observe   - noisy sensor models (UAV multispectral, soil probes)
  anomalies - statistical detection (climatology z-scores, SPI, control charts, spatial outliers)
  forecast  - Monte Carlo ensembles of the rest of the season (yield and maturity distributions)

Pure numpy/scipy; no I/O except the optional calibration fetchers in climate/soil.
"""
