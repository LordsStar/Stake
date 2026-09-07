# Blindado v6.2 — correcciones implementadas

## Blindado v6.2.3 — corrección del cálculo de Brier de producción

Diagnóstico externo (revisado y verificado línea por línea contra el código
real, no aceptado a ciegas):

1. **Contaminación de arranque en frío (confirmado, era peor de lo
   documentado):** `EloModel.update()` agregaba CADA partido al historial de
   Brier, incluidos los primeros partidos de cualquier equipo nuevo —
   predichos con `ELO_INITIAL` para ambos lados (esencialmente una moneda
   ajustada solo por ventaja de local). Esos errores de arranque quedaban
   en el promedio para siempre, sesgando el gate hacia arriba de forma
   permanente. `elo_backtest.py` (offline) ya evitaba esto con
   `brier_solo_calibrados`, pero esa corrección nunca se aplicó al gate de
   PRODUCCIÓN (`probability_for()`/`coverage()`), que es lo que de verdad
   decide `modelo_activo`.
2. **`BRIER_MIN=8` insuficiente (confirmado con simulación):** con un
   modelo perfectamente calibrado a p=0.58 (ventaja de local típica), el
   Brier medido con n=8 varía entre 0.196 y 0.296 en el 90% de los casos —
   ese rango cubre casi todo el margen entre "razonable" y "peor que
   50/50". Con n=30 baja a ~[0.219, 0.267]: sigue siendo ruidoso, pero ya
   no certifica ni rechaza un modelo por pura casualidad de 8 partidos.
3. **Misma ventaja de local para los 16 deportes** y **empate registrado
   como 0.5 en un Brier binario para fútbol** (el gate que decide
   `modelo_activo` solo evalúa la mitad binaria de `probabilities_three_way()`,
   sin dar crédito a la habilidad de predecir empates que sí se calcula
   por separado vía `draw_stats`): diagnóstico correcto, **pendiente**,
   ver abajo.

Corrección aplicada:
- `EloModel.update()` ahora solo agrega un partido al Brier cuando AMBOS
  equipos ya tenían `>= ELO_MIN_GAMES` partidos ANTES de ese partido
  (probado: con dos equipos nuevos jugando entre sí, las primeras 5
  entradas quedan fuera del Brier; la sexta en adelante sí entra).
  `draw_stats` no se filtra igual a propósito — mide la tasa de empate de
  la competencia, no depende de si esos dos equipos ya están calibrados.
- `BRIER_MIN`: 8 -> 30.
- `ELO_SCHEMA_VERSION`: 2 -> 3. Esto fuerza a `elo_trainer.py` a
  reconstruir el Elo completo desde `results.json` con la lógica
  corregida en la próxima corrida — el mecanismo de migración ya existía
  (se usó para separar Elo por liga en v6.2), no hubo que construir nada
  nuevo. Mientras el workflow no corra, `build_elo_model()` sigue
  devolviendo "sin modelo" de forma segura (`schema_is_current()` en
  `False` bloquea cualquier uso del estado viejo).

**No se sabe todavía si el Brier de NFL/MLB sube o baja** después de este
fix — el número actual (0.2359/0.2516) estaba sesgado por construcción,
así que cualquier valor que salga tras el reentrenamiento es más honesto,
pero no hay forma de predecir la dirección sin correrlo contra el
histórico real.

### Pendiente — requiere una decisión de diseño, no solo código

No implementado todavía porque cada uno implica elegir un parámetro que
no me corresponde fijar sin acuerdo:
- **Brier Skill Score contra un baseline por competencia** (ej. "predecir
  siempre el % de victorias de local de esa liga") en vez de un umbral
  absoluto igual para los 16 deportes. Metodológicamente más correcto que
  `BRIER_MAX=0.23` fijo, pero requiere decidir qué baseline usar por
  deporte.
- **Ventaja de local configurable por deporte** en vez de `ELO_HOME=50`
  global (tiene sentido en NFL/fútbol; no necesariamente en tenis, MMA,
  boxeo, cricket en cancha neutral o esports). `elo_backtest.py` ya
  permite explorar esto por namespace.
- **Ventana móvil de Brier** (ej. últimas 300 predicciones) en vez de
  histórico acumulado sin límite, para que la calibración refleje forma
  reciente y no quede atada para siempre a partidos de hace años.
- **Brier multiclase real para fútbol** en vez de tratar empate como 0.5
  dentro de un Brier binario.

## Blindado v6.2.2 — snapshot.json superaba el límite de 25 MB

Reportado en producción: al fallar Bovada solo para `soccer_all`, la app
intentó recuperar esa única referencia desde `snapshot.json`, pero
`obtener_snapshot_remoto()` rechazó el archivo completo por superar 25 MB
— tumbando el fallback para TODO el snapshot, no solo para soccer.

Causa raíz: `market_snapshot_job.py` serializaba TODOS los mercados
activos de cada evento (moneyline, totales, hándicaps, props de jugador,
correct-score, etc.) con `event_to_dict()`. Un solo evento con props
individuales puede tener cientos de mercados; multiplicado por ~150
eventos en 16 deportes, eso desbordaba el límite. El motor de picks
(`market_odds()`/`build_elo_model()`) nunca usó nada fuera de
`moneyline`/`draw_no_bet` — esas líneas extra viajaban en el snapshot sin
que nada las consumiera.

Corrección:
- Nueva función `event_to_dict_pick_markets()` en `blindado_core.py`
  (filtra a `moneyline`/`draw_no_bet`; `event_to_dict()` sin argumentos
  sigue sirviendo completo para `build_prompt()`, sin cambios ahí).
- `market_snapshot_job.py` ahora usa el serializador liviano para
  `stake_events` y `bovada_events`; imprime el tamaño final del snapshot
  y avisa si se acerca al límite de 25 MB otra vez.
- `app.py`: la pestaña "Eventos normalizados" avisa explícitamente cuando
  los datos cargados vienen del snapshot (mercados limitados a
  moneyline/DNB), para no perder de vista ese trade-off.

Prueba con un evento sintético de 300 props (nivel de lo visto en
producción para esports): 162 KB -> 0.4 KB, 99.7% de reducción. No se
tocó el límite de 25 MB en `cloud_snapshot_reader.py` — se resolvió la
causa, no el síntoma.

## Blindado v6.2.1 — corrección puntual (sin tocar gates ni umbrales)

Diagnóstico sobre una corrida real de 147 eventos: 18 se descartaban como
`deporte_o_liga_no_compatible` antes de intentar Elo o Bovada siquiera.
Causa: `BOVADA_ENDPOINTS` tenía fallback genérico `"<deporte>_all"` para
soccer/tennis/mma/boxing/cricket/rugby/volleyball/table-tennis/esports,
pero NO para basketball/baseball/ice-hockey/american-football. Cualquier
liga de esos cuatro deportes que no fuera exactamente NBA/MLB/NHL/NFL/NCAAF
(WNBA, KHL, VHL, CFL, Triple-A, Euroliga, FIBA, NCAA, amistosos de clubes)
quedaba excluida sin oportunidad de emparejar con Bovada.

Corrección en `blindado_core.py`:
- Se agregó `basketball_wnba` como clave específica (WNBA sí suele tener
  mercado propio en Bovada).
- Se agregaron los 4 fallbacks genéricos faltantes: `basketball_all`,
  `baseball_all`, `ice-hockey_all`, `american-football_all`.
- Se extendió `KNOWN_LEAGUE_CODES` con `wnba`.
- `BOVADA_KEY_SPORT` actualizado para que el `NormalizedEvent` resultante
  de Bovada conserve el mismo slug de deporte que usa Stake (evita
  desalineación en `sport_family()`/`match_event_scored()`).

Estos genéricos son *best-effort*: Bovada puede no tener cobertura real
para ligas muy nicho (KHL, VHL, Triple-A) — en ese caso el evento sigue
descartándose, pero ahora en el gate de liquidez con el motivo correcto
("sin referencia secundaria disponible"), no en un gate de compatibilidad
que ni siquiera lo intentaba. No se tocó `BRIER_MAX`, `ELO_K` ni
`ELO_HOME` — eso requiere evidencia de `elo_backtest.py` (nuevo, ver
`README_DEPLOY.md`), no un ajuste reactivo.

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
