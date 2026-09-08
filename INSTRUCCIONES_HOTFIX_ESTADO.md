# Hotfix del error en “Guardar estado público”

El pipeline y el entrenamiento terminaron bien. Falló el commit porque
`state/team_aliases.json` es privado y está ignorado por `.gitignore`.

Reemplaza:

- `app.py` en la raíz.
- `blindado_core.py` en la raíz.
- `reconcile_aliases.py` en la raíz.
- `elo_training.yml` dentro de `.github/workflows/`.

Después ejecuta otra vez **Resultados y entrenamiento Elo Blindado** con
`full_backfill=false` y `max_leagues=24`.

La versión corregida guarda los alias automáticos en
`state/auto_team_aliases.json` y mantiene los alias manuales privados en
`state/team_aliases.json`.
