# Blindado v7.2

- Mantiene Elo schema 4 y sus gates Brier/BSS.
- Conserva el catálogo dinámico de Stake y el snapshot compacto ML/DNB.
- Corrige el límite global del snapshot a 120 minutos, sin relajar la frescura
  individual de cada evento.
- Añade `results_pipeline.py` como orquestador idempotente.
- Añade backfill inicial y actualización ESPN de siete días.
- Rota TheSportsDB por todas las ligas objetivo usando un cursor persistente.
- Añade ATP/WTA mediante ESPN, Oracle's Elixir y paginación OpenDota.
- Corrige la URL pública actual de Oracle's Elixir y reintenta Cricsheet
  validando la firma ZIP.
- Amplía NBA/NHL/MLB y procesa hasta tres temporadas de TheSportsDB.
- Conserva Cricsheet para cricket.
- Añade reconciliación automática conservadora de nombres y reporte de alias
  ambiguos.
- Separa `auto_team_aliases.json` (público y generado por Actions) de
  `team_aliases.json` (manual y privado), evitando el fallo de `git add`.
- Actualiza resultados, alias y Elo cada seis horas mediante GitHub Actions.
- El panel diferencia una ruta Bovada configurada de una coincidencia real.
- El panel explica que el snapshot solo transporta moneyline/DNB.

Las fuentes gratuitas pueden fallar o no cubrir ligas minoritarias. El pipeline
continúa con las demás fuentes y nunca crea resultados ni alias ambiguos.
