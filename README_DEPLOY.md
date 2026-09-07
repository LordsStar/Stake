# Blindado v6 — instalación en GitHub y Streamlit Cloud

## 1. Subir a GitHub

Extrae el ZIP y sube **el contenido de la carpeta**, no el ZIP cerrado.
`app.py`, `blindado_core.py` y `requirements.txt` deben quedar en la raíz
del repositorio. Conserva también la ruta `.github/workflows/elo_training.yml`.

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

## Archivos principales

- `app.py`: interfaz que inicia Streamlit.
- `blindado_core.py`: Stake, Bovada, Elo, filtros y selección.
- `cloud_snapshot_reader.py`: respaldo opcional desde snapshot.
- `fetch_results_espn.py`: ingesta automática inicial de NBA/NFL/NHL/MLB.
- `elo_trainer.py`: entrenamiento cronológico e idempotente.
- `.github/workflows/elo_training.yml`: ejecución automática diaria.
- `results_schema_example.csv`: plantilla para otros deportes.
