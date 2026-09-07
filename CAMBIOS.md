# Blindado v6.2 — correcciones implementadas

## Flujo actual

- Stake Sports Data API sigue siendo la fuente principal de eventos, mercados,
  selecciones, cuotas y timestamps.
- Bovada es solamente referencia secundaria gratuita para liquidez y
  divergencia; nunca se mezcla con Stake para fabricar el modelo.
- Elo se entrena cronológicamente con resultados finales reales y separados en
  namespaces estables por liga, circuito, formato o videojuego.
- El resultado sigue siendo como máximo una selección o `NINGUNO`.

## Correcciones

| Problema | Corrección v6.2 |
|---|---|
| Elo definido pero nunca entrenado | `elo_trainer.py`, histórico normalizado y workflow diario |
| Elo mezclado entre competiciones | Schema Elo 2 y namespaces estables; ATP/WTA, combate, cricket y esports ya no se fragmentan por torneo puntual |
| Un resultado histórico nuevo podía aplicarse fuera de orden | El workflow reconstruye todo el Elo cronológicamente en cada corrida |
| Fútbol sin modelo de empate | Elo 1X2 aproxima la tasa de empate con el histórico de la competición |
| Fallback Bovada no se activaba ante 403/429 | La app detecta fallos parciales y recupera las referencias desde `snapshot.json` |
| Snapshot dependía de PC local | `market_snapshot_job.py` + workflow cloud cada 30 minutos |
| Cobertura limitada a ligas estadounidenses | Conectores secundarios para fútbol, tenis, MMA, boxeo, cricket, rugby, voleibol, tenis de mesa y esports |
| Resultados limitados a ESPN | Se agregaron TheSportsDB, Cricsheet y OpenDota, todos gratuitos |
| Estado privado se perdía al reiniciar Streamlit | Exportación/restauración manual en JSON; nunca se publica en el repositorio público |
| Historial remoto de movimiento era descartado | Ahora se fusiona y deduplica antes de añadir la observación actual |
| Catálogo de promociones no persistía | Catálogo público versionado; elegibilidad y promociones personales siguen privadas y manuales |
| Acciones antiguas generaban warning de Node 20 | `actions/checkout@v5` y `actions/setup-python@v6` |
| Dos workflows podían empujar simultáneamente | Grupo de concurrencia compartido y `git pull --rebase` antes de escribir |

## Límites que se mantienen por integridad

- Se descargan todos los mercados activos devueltos por Stake, pero el motor
  solo evalúa moneyline y draw-no-bet. Hándicaps, totales y props se muestran,
  no producen picks todavía.
- Tener un conector para un deporte no garantiza cobertura de cada liga. Si no
  hay cinco resultados por participante, ocho predicciones históricas, Brier
  menor o igual a 0.23 o referencia Bovada fresca, el evento se descarta.
- Tenis, MMA y boxeo requieren además confirmación reciente del estado físico
  con una fuente aportada por el usuario.
- El catálogo público de promociones es informativo. Solo una promoción
  personal, cuantificada y confirmada puede modificar el EV.
- Los endpoints públicos pueden cambiar o bloquear runners cloud. Por eso se
  conserva el snapshot y se informa el fallo; nunca se sustituyen datos reales
  por valores inventados.

## Automatización incluida

- `.github/workflows/market_snapshot.yml`: mercados Stake/Bovada cada 30 min.
- `.github/workflows/elo_training.yml`: resultados y reentrenamiento diario.
- Ambos usan el `GITHUB_TOKEN` automático. En repositorios públicos no se
  necesita un token personal ni credenciales de Stake.
