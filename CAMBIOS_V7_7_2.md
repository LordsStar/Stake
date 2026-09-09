# Blindado v7.7.2 — histéresis basada en evidencia persistente

## Causa corregida

`results_pipeline.py` reconstruía Elo mediante `elo_trainer.py --reset`, y el
trainer eliminaba `activation_state`. Por eso un modelo podía pasar de inactivo
a `pass_streak: 3` dentro de una sola ejecución.

## Política v2

- `ELO_SCHEMA_VERSION = 5`.
- `BRIER_ACTIVATION_POLICY_VERSION = 2`.
- Cada predicción madura guarda el `match_id` del resultado que la produjo.
- El fingerprint es SHA-256 del conjunto ordenado de `match_id` en la ventana Brier.
- Brier y skill siempre se recalculan; nunca se restauran métricas antiguas.
- `--reset` reconstruye ratings/Brier/processed, pero preserva
  `activation_state` por namespace.
- Un streak solo avanza cuando cambia el fingerprint.
- Tres workflows sobre el mismo conjunto de partidos cuentan como una sola
  evidencia, no como tres confirmaciones.
- `--reset-activation` permite borrar la histéresis únicamente de forma
  explícita.
- El botón manual de Streamlit usa la misma reconstrucción segura; tampoco
  elimina `activation_state` por omisión.

## Migración

La primera corrida con schema 5 reconstruye el historial para agregar IDs. Los
namespaces v1 conservan su estado operativo y fijan el fingerprint como línea
base sin avanzar streaks. Un namespace realmente nuevo empieza inactivo con
`pass_streak=0` y `fail_streak=0`.

## Liquidez

Se añade `bovada_sin_mercado_principal` para el caso donde el evento coincide,
pero Bovada no trae moneyline/DNB. Ya no se mezcla con selecciones existentes
cuyos nombres no logran emparejarse.

## Sin cambios

- Matcher y sus umbrales.
- Fórmula/umbral de confianza.
- Frescura y detector de caché.
- Modelo y validación MLB.
- Frecuencia de workflows.
