# Blindado v7.2

- Mantiene Elo schema 4 y sus gates Brier/BSS.
- Conserva el catálogo dinámico de Stake y el snapshot compacto ML/DNB.
- Corrige el límite global del snapshot a 120 minutos, sin relajar la frescura
  individual de cada evento.
- Añade `results_pipeline.py` como orquestador idempotente.
- Añade backfill inicial y actualización ESPN de siete días.
- Rota TheSportsDB por todas las ligas objetivo usando un cursor persistente.
- Añade históricos ATP/WTA, Oracle's Elixir y paginación OpenDota.
- Conserva Cricsheet para cricket.
- Añade reconciliación automática conservadora de nombres y reporte de alias
  ambiguos.
- Actualiza resultados, alias y Elo cada seis horas mediante GitHub Actions.
- El panel diferencia una ruta Bovada configurada de una coincidencia real.
- El panel explica que el snapshot solo transporta moneyline/DNB.

Las fuentes gratuitas pueden fallar o no cubrir ligas minoritarias. El pipeline
continúa con las demás fuentes y nunca crea resultados ni alias ambiguos.
