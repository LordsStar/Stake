# Backtest offline de Elo — 2026-09-14T16:01:39.134357+00:00

Grid: K en [10, 15, 20, 25, 30, 40] × ventaja_local en [0, 25, 50, 65, 75, 100] (36 combinaciones por namespace)

## american-football:nfl (670 partidos históricos)
- **Producción actual** K=20 home=50 -> brier_todos=0.234 brier_solo_calibrados=0.2387 baseline=0.2475 skill=0.0355 activo_schema4=True (equipos_calibrados=32/34, muestras_calibradas=200)
- **Mejor del grid** K=40 home=0 -> brier_todos=0.2292 brier_solo_calibrados=0.232 baseline=0.2475 skill=0.0626 activo_schema4=True (equipos_calibrados=32/34)
- Delta vs. producción: +0.0067 -> mejora relevante y conserva/supera el gate; candidata a revisión manual

## baseball:mlb (2694 partidos históricos)
- **Producción actual** K=20 home=20 -> brier_todos=0.2491 brier_solo_calibrados=0.2519 baseline=0.2475 skill=-0.0177 activo_schema4=False (equipos_calibrados=30/32, muestras_calibradas=200)
- **Mejor del grid** K=10 home=25 -> brier_todos=0.2462 brier_solo_calibrados=0.246 baseline=0.2475 skill=0.0061 activo_schema4=False (equipos_calibrados=30/32)
- Delta vs. producción: +0.0059 -> mejora numérica, pero no activa el modelo; insuficiente para cambiar producción

## basketball:nba (1400 partidos históricos)
- **Producción actual** K=20 home=60 -> brier_todos=0.214 brier_solo_calibrados=0.1901 baseline=0.2436 skill=0.2195 activo_schema4=True (equipos_calibrados=30/37, muestras_calibradas=200)
- **Mejor del grid** K=40 home=65 -> brier_todos=0.2135 brier_solo_calibrados=0.1869 baseline=0.2436 skill=0.233 activo_schema4=True (equipos_calibrados=30/37)
- Delta vs. producción: +0.0032 -> mejora relevante y conserva/supera el gate; candidata a revisión manual

## cricket:all (133 partidos históricos)
- **Producción actual** K=20 home=0 -> brier_todos=0.2298 brier_solo_calibrados=0.2169 baseline=0.2194 skill=0.0113 activo_schema4=False (equipos_calibrados=35/39, muestras_calibradas=29)
- No hay suficientes muestras calibradas para comparar el grid en este namespace.

## cricket:t20 (226 partidos históricos)
- **Producción actual** K=20 home=0 -> brier_todos=0.2417 brier_solo_calibrados=0.2456 baseline=0.2469 skill=0.0053 activo_schema4=False (equipos_calibrados=52/83, muestras_calibradas=36)
- **Mejor del grid** K=25 home=0 -> brier_todos=0.2404 brier_solo_calibrados=0.2454 baseline=0.2469 skill=0.006 activo_schema4=False (equipos_calibrados=52/83)
- Delta vs. producción: +0.0002 -> mejora marginal/ruido — no justifica cambiar producción

## dota-2:all (438 partidos históricos)
- **Producción actual** K=20 home=0 -> brier_todos=0.2425 brier_solo_calibrados=0.2387 baseline=0.2499 skill=0.0448 activo_schema4=True (equipos_calibrados=53/136, muestras_calibradas=200)
- **Mejor del grid** K=40 home=0 -> brier_todos=0.2406 brier_solo_calibrados=0.2368 baseline=0.2499 skill=0.0525 activo_schema4=True (equipos_calibrados=53/136)
- Delta vs. producción: +0.0019 -> mejora marginal/ruido — no justifica cambiar producción

## ice-hockey:nhl (1499 partidos históricos)
- **Producción actual** K=20 home=25 -> brier_todos=0.2481 brier_solo_calibrados=0.2382 baseline=0.2499 skill=0.0469 activo_schema4=True (equipos_calibrados=32/32, muestras_calibradas=200)
- **Mejor del grid** K=25 home=0 -> brier_todos=0.2497 brier_solo_calibrados=0.2375 baseline=0.2499 skill=0.0497 activo_schema4=True (equipos_calibrados=32/32)
- Delta vs. producción: +0.0007 -> mejora marginal/ruido — no justifica cambiar producción

## tennis:atp (3958 partidos históricos)
- **Producción actual** K=20 home=0 -> brier_todos=0.242 brier_solo_calibrados=0.2148 baseline=0.2499 skill=0.1403 activo_schema4=True (equipos_calibrados=430/763, muestras_calibradas=200)
- **Mejor del grid** K=40 home=0 -> brier_todos=0.2406 brier_solo_calibrados=0.2058 baseline=0.2499 skill=0.1765 activo_schema4=True (equipos_calibrados=430/763)
- Delta vs. producción: +0.0090 -> mejora relevante y conserva/supera el gate; candidata a revisión manual

## tennis:wta (5271 partidos históricos)
- **Producción actual** K=20 home=0 -> brier_todos=0.2434 brier_solo_calibrados=0.2306 baseline=0.25 skill=0.0775 activo_schema4=True (equipos_calibrados=563/995, muestras_calibradas=200)
- **Mejor del grid** K=40 home=0 -> brier_todos=0.2425 brier_solo_calibrados=0.2269 baseline=0.25 skill=0.0922 activo_schema4=True (equipos_calibrados=563/995)
- Delta vs. producción: +0.0037 -> mejora relevante y conserva/supera el gate; candidata a revisión manual

Este reporte NO modificó `state/elo_state.json` ni `BRIER_MAX`. Es solo evidencia numérica para decidir si conviene ajustar `ELO_K`/`ELO_HOME_BY_SPORT` en `blindado_core.py` — y con qué respaldo, en vez de tocar el umbral del Brier a ciegas cada vez que sale `NINGUNO`.
