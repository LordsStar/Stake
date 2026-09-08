# Instalación de Blindado v7.2

## Archivos que se sustituyen en la raíz

- `app.py`
- `blindado_core.py`
- `cloud_snapshot_reader.py`
- `market_snapshot_job.py`
- `fetch_results_espn.py`
- `fetch_results_free.py`
- `elo_trainer.py`
- `elo_backtest.py`
- `requirements.txt`

## Archivos nuevos en la raíz

- `results_pipeline.py`
- `reconcile_aliases.py`

## Workflows

Sustituye o crea exactamente:

- `.github/workflows/market_snapshot.yml`
- `.github/workflows/elo_training.yml`
- `.github/workflows/elo_backtest.yml`

No borres `snapshot.json`, `state/results/results.json` ni
`state/elo_state.json`: el pipeline los amplía y reconstruye sin perder el
histórico válido.

## Primera ejecución

1. En GitHub, confirma **Settings > Actions > General > Workflow permissions >
   Read and write permissions**.
2. Ejecuta **Actions > Snapshot de mercados Blindado > Run workflow**.
3. Cuando termine, ejecuta **Actions > Resultados y entrenamiento Elo
   Blindado > Run workflow** con `full_backfill=true` y `max_leagues=24`.
4. Espera que termine y comprueba que haya creado un commit llamado
   `Actualiza resultados, alias y Elo`.
5. En Streamlit pulsa **Reboot app**.

Desde ese momento los workflows actualizan automáticamente los datos. Para el
uso diario solo abre Streamlit, actualiza los datos si corresponde y pulsa
**Ejecutar Blindado v7.2**.

## Acciones que siguen siendo manuales

- Confirmar que una promoción personal aplica a tu cuenta.
- Verificar estado físico reciente para tenis, MMA y boxeo.
- Revisar alias ambiguos que aparezcan en `state/unresolved_aliases.json`.

No es necesario mantener una computadora encendida ni ejecutar scripts
localmente.
