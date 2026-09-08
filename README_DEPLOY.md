# Blindado v7.2 — instalación en GitHub y Streamlit Cloud

## Qué queda automatizado

- Snapshot Stake/Bovada cada 30 minutos.
- Resultados, reconciliación segura de nombres y Elo cada 6 horas.
- Backfill inicial de las ligas ESPN principales.
- Rotación persistente por las ligas activas de Stake.
- ESPN ATP/WTA, Cricsheet, OpenDota, Oracle's Elixir y TheSportsDB como fuentes
  gratuitas adicionales.

En el uso diario solo se abre Streamlit y se pulsa **Ejecutar Blindado**. La
elegibilidad de promociones personales y el estado físico de tenis/MMA/boxeo
siguen siendo confirmaciones manuales.

## 1. Subir a GitHub

Extrae el ZIP y sube **el contenido de la carpeta**, no el ZIP cerrado.
`app.py`, `blindado_core.py` y `requirements.txt` deben quedar en la raíz
del repositorio. Conserva también `.github/workflows/elo_training.yml` y
`.github/workflows/market_snapshot.yml`. El segundo genera cada 30 minutos el
respaldo público usado cuando Bovada directo falla en Streamlit.

## 2. Permitir que GitHub Actions guarde el Elo

En GitHub abre:

`Settings > Actions > General > Workflow permissions`

Selecciona **Read and write permissions** y guarda. El workflow usa el
`GITHUB_TOKEN` automático; no debes crear ni compartir un token personal.

Después abre:

`Actions > Resultados y entrenamiento Elo Blindado > Run workflow`

La primera ejecución hace el backfill principal. Las siguientes revisan los
últimos siete días y rotan por 24 ligas del snapshot. El workflow guarda
resultados, Elo, alias seguros, pendientes de alias y cobertura de fuentes.

El modelo se reconstruye completo en cada corrida. Esto mantiene el orden
cronológico cuando una fuente descubre tarde un resultado histórico anterior.
Blindado v7.2 conserva Elo schema 4 con ventaja local por deporte. Una
instalación anterior se reconstruye automáticamente en la primera corrida.

Después de instalar v7, ejecuta el entrenamiento manualmente una vez. Detectará
el formato anterior y reconstruirá el Elo con namespaces estables por liga/circuito.

Ejecuta también una vez `Actions > Snapshot de mercados Blindado > Run workflow`
antes del entrenamiento. Ese archivo permite descubrir las ligas activas de
Stake y mantener un respaldo de Bovada sin dejar una computadora encendida.

## 3. Crear la app en Streamlit Community Cloud

En `share.streamlit.io` selecciona:

- Repository: `tu_usuario/tu_repositorio`
- Branch: `main`
- Main file path: `app.py`

Pulsa **Deploy**. Streamlit instalará automáticamente lo indicado en
`requirements.txt`.

## 4. Secrets opcionales

No se necesita usuario, contraseña, cookie ni sesión de Stake. La Sports Data
API funciona actualmente sin clave.

Solo si utilizas el snapshot remoto, abre en Streamlit:

`App > Settings > Secrets`

Y agrega:

```toml
SNAPSHOT_REPO = "tu_usuario/tu_repositorio"
SNAPSHOT_PATH = "snapshot.json"
SNAPSHOT_BRANCH = "main"
```

Para un repositorio público no hace falta `SNAPSHOT_GITHUB_TOKEN`. Para uno
privado sí sería necesario un token con acceso mínimo de lectura, guardado solo
en Streamlit Secrets; nunca debe escribirse en el repositorio.

## 5. Uso diario

1. Abre la app.
2. Selecciona deportes.
3. Pulsa **Actualizar datos deportivos**.
4. Revisa el panel de salud.
5. En tenis/MMA/boxeo registra estado físico con fuente y fecha.
6. Confirma manualmente cualquier promoción aplicable.
7. Ejecuta Blindado v7.

`NINGUNO` es el resultado correcto cuando Elo, Bovada, frescura, liquidez o
los gates obligatorios no tienen evidencia suficiente.

### Promociones

- El catálogo público se carga siempre desde `state/public_promotions.json`.
- La elegibilidad se confirma manualmente en la sesión y no se publica en GitHub.
- El catálogo público es informativo y no modifica el EV automáticamente.
- Solo una promoción personal añadida, confirmada y cuantificada puede modificar el cálculo.

No guardes usuario, contraseña, cookies, tier VIP ni información privada en un repositorio público.

### Cobertura de mercados

El workflow descubre `/sports` dinámicamente y recorre la jerarquía oficial
`deporte > categoría > torneo > fixtures`. No depende de una lista fija ni
del endpoint corto que normalmente devuelve 10 eventos. El snapshot guarda
todos los eventos prepartido utilizables encontrados, pero solo sus mercados
`moneyline` y `draw_no_bet` para mantenerse por debajo de 25 MB. En consulta
directa, la pestaña de inspección muestra todos los mercados activos recibidos.

El motor de picks utiliza únicamente `moneyline` y `draw_no_bet`, porque el
Elo actual predice ganador. Totales, hándicaps y props no entran al motor hasta
que exista un modelo estadístico específico para esas líneas.

### Cobertura deportiva gratuita

Stake sigue siendo la base principal. El workflow consulta todos los deportes
habilitados que `/sports` reporte en ese momento, incluidos nuevos esports;
no hay que editar Python cuando Stake añade un slug. Esto cubre el catálogo de
apuestas deportivas, no los juegos de casino/slots. Para resultados se usan
exclusivamente fuentes gratuitas:

- ESPN para NBA, NFL, NHL y MLB.
- TheSportsDB (API v1 pública) como ingesta multideporte por liga.
- Cricsheet JSON para cricket reciente.
- OpenDota para partidos profesionales de Dota 2.

`state/source_coverage.json` registra qué ligas encontró o rechazó la fuente
gratuita. Descubrir un evento de Stake no significa que automáticamente tenga
histórico Elo: esports pequeños y ligas nicho pueden seguir sin resultados
gratuitos verificables. En ese caso se descartan de forma explícita.

El conector no equivale a prometer un pick. Cada evento todavía necesita
resultados suficientes para ambos participantes, calibración superior al
baseline en una ventana reciente, moneyline o
DNB en Stake, una coincidencia fresca en Bovada y todos los gates aplicables.
Cuando una fuente gratuita no cubre una liga concreta, la app muestra
`Elo ausente o no calibrado` en vez de inventar datos.

### Estado privado y reinicios de Streamlit

La pestaña `Estado privado` permite descargar y restaurar alias, promociones
personales, verificaciones físicas e historial de movimiento. Conserva ese
respaldo en tu dispositivo y no lo subas al repositorio público.

## Archivos principales

- `app.py`: interfaz que inicia Streamlit.
- `blindado_core.py`: Stake, Bovada, Elo, filtros y selección.
- `cloud_snapshot_reader.py`: respaldo opcional desde snapshot.
- `fetch_results_espn.py`: ingesta automática inicial de NBA/NFL/NHL/MLB.
- `fetch_results_free.py`: TheSportsDB + Cricsheet + OpenDota + Oracle's
  Elixir, sin API de pago.
- `reconcile_aliases.py`: crea alias únicamente con coincidencias fuertes.
- `results_pipeline.py`: ejecuta resultados, alias y Elo en el orden correcto.
- `elo_trainer.py`: entrenamiento cronológico e idempotente.
- `.github/workflows/elo_training.yml`: ejecución automática cada 6 horas.
- `.github/workflows/market_snapshot.yml`: snapshot público cada 30 minutos.
- `.github/workflows/elo_backtest.yml`: reporte semanal de calibración, sin cambiar parámetros.
- `market_snapshot_job.py`: genera el snapshot sin login ni datos personales.
- `elo_backtest.py`: genera el reporte que usa el workflow semanal.
- `state/public_promotions.json`: catálogo público persistente, sin datos personales.
- `results_schema_example.csv`: plantilla para otros deportes.
