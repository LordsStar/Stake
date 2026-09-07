# Blindado v6 — instalación en GitHub y Streamlit Cloud

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

`Actions > Entrenamiento Elo Blindado > Run workflow`

La primera ejecución busca un histórico inicial. Las siguientes solo revisan
los últimos tres días. El workflow guardará `state/results/results.json` y
`state/elo_state.json` en el repositorio.

El modelo se reconstruye completo en cada corrida. Esto mantiene el orden
cronológico cuando una fuente descubre tarde un resultado histórico anterior.

Después de instalar v6.2, ejecútalo manualmente una vez. Detectará el formato
anterior y reconstruirá el Elo con namespaces estables por liga/circuito.

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
7. Ejecuta Blindado v6.

`NINGUNO` es el resultado correcto cuando Elo, Bovada, frescura, liquidez o
los gates obligatorios no tienen evidencia suficiente.

### Promociones

- El catálogo público se carga siempre desde `state/public_promotions.json`.
- La elegibilidad se confirma manualmente en la sesión y no se publica en GitHub.
- El catálogo público es informativo y no modifica el EV automáticamente.
- Solo una promoción personal añadida, confirmada y cuantificada puede modificar el cálculo.

No guardes usuario, contraseña, cookies, tier VIP ni información privada en un repositorio público.

### Cobertura de mercados

La app conserva y muestra todos los mercados activos que la Sports Data API
devuelve para cada fixture recibido. El motor de picks utiliza únicamente
`moneyline` y `draw_no_bet`. Totales, hándicaps y props se muestran en la
inspección, pero todavía no entran al modelo de selección.

### Cobertura deportiva gratuita

Stake sigue siendo la base principal y el workflow solicita estos slugs:
fútbol, baloncesto, béisbol, hockey, fútbol americano, tenis, MMA, boxeo,
cricket, rugby, voleibol, tenis de mesa, Counter-Strike, Dota 2, League of
Legends y Valorant. Para resultados se usan exclusivamente fuentes gratuitas:

- ESPN para NBA, NFL, NHL y MLB.
- TheSportsDB (API v1 pública) como ingesta multideporte por liga.
- Cricsheet JSON para cricket reciente.
- OpenDota para partidos profesionales de Dota 2.

El conector no equivale a prometer un pick. Cada evento todavía necesita
resultados suficientes para ambos participantes, Brier calibrado, moneyline o
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
- `fetch_results_free.py`: TheSportsDB + Cricsheet + OpenDota, sin API de pago.
- `elo_trainer.py`: entrenamiento cronológico e idempotente.
- `.github/workflows/elo_training.yml`: ejecución automática diaria.
- `.github/workflows/market_snapshot.yml`: snapshot público cada 30 minutos.
- `market_snapshot_job.py`: genera el snapshot sin login ni datos personales.
- `state/public_promotions.json`: catálogo público persistente, sin datos personales.
- `results_schema_example.csv`: plantilla para otros deportes.
