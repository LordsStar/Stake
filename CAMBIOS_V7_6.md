# Blindado v7.6 — observabilidad y frescura real de mercados

## Resultado

La frescura ya no se decide con el timestamp de la última modificación de
cuota enviado por Stake/Bovada. La autoridad es la hora de la última consulta
exitosa (`market_fetched_at`). Una línea puede permanecer estable durante horas
y seguir fresca si fue observada recientemente.

## Snapshot schema 5

Cada evento conserva:

- `market_fetched_at`
- `source_last_update_at`
- `market_last_changed_at`
- `fetch_status`
- `carried_forward`
- `unchanged_fetches`
- `audit_flags`

El snapshot agrega `snapshot_generated_at`, historial de las últimas diez
corridas, intervalo esperado calculado por mediana y auditoría de
observabilidad por proveedor.

## Límites relativos

- Evento a 60 minutos o menos: observación máxima de 10 minutos.
- Evento entre 1 y 6 horas: observación máxima de 30 minutos.
- Evento a más de 6 horas: observación máxima de 120 minutos.

## Estados

- `fresh_changed`
- `fresh_stable`
- `possible_cache_upstream` (advertencia, no bloquea)
- `cache_inconclusive_low_cohort` (informativo)
- `source_timestamp_missing` (advertencia)
- `fetch_failed_recent_fallback` (advertencia, no bloquea mientras siga fresco)
- `carried_forward_stale` (bloquea)
- `market_observation_expired` (bloquea)
- `snapshot_expired` (bloqueo global en la app)

## Detector no bloqueante de posible caché

Requiere simultáneamente timestamp upstream congelado entre 90 y 180 minutos,
cuatro consultas consecutivas sin cambios, al menos ocho mercados comparables,
cuatro comparables actualizados y 40% de actividad dentro de una ventana real
de 60 minutos.

La cohorte comparte proveedor, deporte, liga, mercado principal y bucket de
tiempo hasta el inicio. Las ligas pequeñas se clasifican como inconclusas y no
se penalizan.

## Fallback

Cuando falla una liga Bovada, el job puede conservar sus eventos previos con
`carried_forward=true`. No actualiza `market_fetched_at`; el motor decide si la
última observación exitosa todavía está dentro del límite.

Stake también conserva únicamente los eventos cuyo fallo de detalle puede
relacionarse de forma exacta por `event_id`. No realiza emparejamientos por
nombres ni rellena ligas completas de forma aproximada.

## Compatibilidad y aislamiento

- Los snapshots schema 4 siguen siendo legibles mediante fallback explícito.
- No se modificó la validación MLB de 100 partidos prospectivos.
- No se modificaron los gates Brier/histéresis de v7.5.
- Se incluyen 18 pruebas automatizadas aprobadas.
