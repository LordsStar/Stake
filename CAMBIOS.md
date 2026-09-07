# Blindado v6 — Cambios respecto a v5.1

## Estructura nueva

Antes había un solo archivo (`app_stake_blindado.py`, ~1600 líneas). Ahora:

```
blindado_core.py                 # toda la lógica, SIN Streamlit (importable desde CI)
app_stake_blindado_v6.py         # la app de Streamlit (solo UI)
elo_trainer.py                   # CLI: entrena el Elo cronológicamente
fetch_results_espn.py            # CLI: descarga resultados finalizados (fase 1: NBA/NFL/NHL/MLB)
results_schema_example.csv       # plantilla de resultados para ingesta manual
state/team_aliases.json          # vacío al inicio — overrides de nombres de equipo
state/team_aliases.example.json  # ejemplo documentado del formato
.github/workflows/elo_training.yml   # corre fetch + entrenamiento todos los días
```

`blindado_core.py` no importa `streamlit` en ningún punto — por eso el
workflow de GitHub Actions solo necesita `pip install requests`, nada de
`streamlit` en el runner.

## Por qué no necesito acceso a tu GitHub

El workflow (`.github/workflows/elo_training.yml`) corre **dentro de tu
propio repositorio**, usando el `GITHUB_TOKEN` que GitHub Actions inyecta
automáticamente en cada ejecución (por eso tiene `permissions: contents:
write` y no pide ningún secret nuevo). Todo el ciclo — descargar
resultados, entrenar el Elo, hacer commit de `state/` — pasa 100% dentro
de tu cuenta, sin que yo necesite ver ni tocar el repo.

Lo único que tienes que hacer:

1. Copiar estos archivos a tu repositorio (mismas rutas relativas).
2. En GitHub → tu repo → **Settings → Actions → General → Workflow
   permissions**, marcar **"Read and write permissions"** (si no, el
   `git push` del workflow fallará por permisos).
3. Listo — corre solo todos los días a las 09:00 UTC, o lo disparas a
   mano desde la pestaña **Actions** (`workflow_dispatch`).

Si en el futuro quieres que yo pueda leer/editar tu repo directamente en
una sesión de chat (por ejemplo para revisar `cloud_snapshot_reader.py`
sin que tengas que subirlo tú), eso sí requeriría conectar un connector
de GitHub — pero para lo que pediste hoy (persistencia del Elo) no hace
falta.

## Diagnóstico → corrección (los ~12 puntos)

| # | Problema (v5.1) | Corrección (v6) |
|---|---|---|
| 1 | Elo nunca se alimentaba (`update()` sin caller) | `train_elo_from_results()` + `fetch_results_espn.py` + workflow diario |
| 2 | Stake+Bovada promediados = "modelo" → EV circular | `market_consensus_info()` es solo informativo; `build_elo_model()` es la única fuente de `P_modelo` |
| 3 | Bovada era modelo Y referencia a la vez | Bovada ahora solo aparece como `reference_prob` / gate de liquidez |
| 4 | Estado físico era una instrucción de texto, no se verificaba | `PhysicalStatusRegistry` — gate obligatorio y duro para tenis/MMA/boxeo |
| 5 | Fuentes eran solo links, sin registro verificable | Se mantienen los links (siguen sin API), pero ahora acompañan al gate de estado físico con timestamp y fuente exigidos |
| 6 | `movement_ok=True` hardcodeado | `movement_status()` calcula sobre historial real (`state/movement_history.json`), nunca premia con 1 sola observación |
| 7 | Frescura solo se filtraba en el snapshot remoto, no en la API directa | `market_freshness_ok()` aplica siempre, con límite más estricto cerca del inicio del evento |
| 8 | Liquidez = `bool(bovada_event)` | `liquidity_status()` exige score de coincidencia >= 0.5 y cuota de Bovada con <120 min de antigüedad |
| 9 | Promociones solo validaban event_id/selection, sin elegibilidad explícita | `select_promotion()` valida sport/mercado y exige `confirmed_eligible=True` |
| 10 | Esports caía en `other` (bug real: el chequeo buscaba un prefijo `"esports"` que ningún slug de Stake tiene) | `sport_family()` corregido + `ESPORT_SOURCES` por videojuego (HLTV/VLR/Oracle's Elixir/OpenDota) |
| 11 | `elo_state.json` solo en disco efímero de Streamlit Cloud | Workflow de GitHub Actions lo commitea al repo — sobrevive redeploys |
| 12 | Una sola `requests.Session` compartida entre hilos | `HttpClient` con sesión por hilo (`threading.local`), caché TTL, reintentos solo 429/5xx respetando `Retry-After`, semáforo global de concurrencia |

### Cambio de comportamiento que debes saber

Antes, un pick podía salir con **Elo solo** o con **Bovada solo** (según
la prioridad a/b/c del prompt original). Ahora el motor exige **ambos a
la vez**: Elo calibrado (modelo) *y* Bovada emparejado y fresco
(liquidez/divergencia). Es una interpretación más estricta que la
original — la razón es que sin una referencia secundaria no hay con qué
validar divergencia ni liquidez de forma seria, y eso es justamente lo
que señalaba el diagnóstico. Consecuencia práctica: **vas a ver más
`NINGUNO` al principio**, hasta que el Elo tenga historial real
entrenado. Es el comportamiento honesto, no un bug.

## Puesta en marcha

1. **Sube estos archivos a tu repo** (reemplaza el `app_stake_blindado.py`
   viejo por `app.py` + `blindado_core.py`; tu
   `cloud_snapshot_reader.py` existente no se tocó, se sigue importando
   igual).
2. **Activa permisos de escritura en Actions** (ver arriba).
3. **Corre el workflow una vez a mano** (Actions → Entrenamiento Elo
   Blindado → Run workflow) para que se genere el primer
   `state/results/results.json` y `state/elo_state.json`.
4. **Streamlit Cloud**: apunta la app a `app.py`.
5. Para deportes fuera de fase 1 (fútbol, tenis, MMA, boxeo, esports):
   sube resultados manualmente desde la pestaña **📈 Resultados / Elo**
   de la app (CSV con el formato de `results_schema_example.csv`), o
   agrega más scripts `fetch_results_<fuente>.py` siguiendo el mismo
   patrón que `fetch_results_espn.py`.
6. Para tenis/MMA/boxeo, usa la pestaña **🩹 Estado físico** antes de
   correr Blindado en esos eventos — si no hay registro `verified_ok`
   vigente, se descartan automáticamente.

## Pendiente conocido (no resuelto hoy, por honestidad)

- **IDs de equipo**: `TeamAliasRegistry` da el mecanismo, pero la base de
  alias en sí (`team_aliases.json`) empieza vacía. Vas a tener que irla
  llenando conforme el sistema te avise (o notes tú) que un mismo equipo
  aparece con dos nombres distintos entre Stake y ESPN.
- **Fuentes fase 2-4** (fútbol, tenis, MMA/boxeo, esports): no hay
  conector automático de resultados todavía — solo ESPN para NBA/NFL/
  NHL/MLB. La ingesta manual por CSV cubre el hueco mientras tanto.
- **Interpretación de dirección del movimiento de línea**: el sistema
  certifica que el movimiento está *verificado* (>=2 observaciones,
  reciente), pero no interpreta si "sube" es señal de dinero sharp en
  contra o de fade del público — eso requeriría más contexto de flujo de
  apuestas del que hay disponible gratis.
