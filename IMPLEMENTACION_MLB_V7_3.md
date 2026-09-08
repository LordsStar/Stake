# Blindado v7.3 — modelo especializado MLB

## Qué cambia

MLB ya no depende del Elo puro para producir una probabilidad. Elo pasa a ser
una de seis señales agregadas del modelo MLB:

1. diferencia Elo con localía;
2. diferencia de calidad de los abridores (FIP reducido);
3. diferencia de calidad de los bullpens (FIP reducido de 30 días);
4. carga de los bullpens durante las últimas 72 horas;
5. diferencia de descanso;
6. diferencia de forma de los últimos 10 partidos.

No se entrenan 15–20 estadísticas correlacionadas. Los componentes internos de
FIP se convierten en un solo índice antes de entrar al modelo y todas las
variables se estandarizan usando únicamente el bloque de entrenamiento.

La selección de regularización utiliza un bloque cronológico de validación. El
Brier publicado se calcula después sobre un bloque de test intacto. El modelo
solo se activa si simultáneamente:

- tiene por lo menos 100 predicciones de test;
- Brier de test <= 0.245;
- skill frente al baseline >= 2%;
- mejora el Elo puro por al menos 0.001 de Brier.

## Alineaciones y lesiones

No son variables de entrenamiento en v1. No existe dentro del proyecto un
histórico gratuito y fiable que indique qué alineación o lesión se conocía en
cada instante prepartido. Usar la alineación final retroactivamente produciría
fuga de información.

Las alineaciones se recopilan prospectivamente. Si faltan 90 minutos o menos y
MLB todavía no publica ambas, el evento se descarta. Las lesiones quedan como
información prospectiva; no aumentan la probabilidad ni la confianza hasta que
exista suficiente histórico fechado para entrenarlas de forma válida.

## Archivos que debes subir/reemplazar

- `app.py`
- `blindado_core.py`
- `market_snapshot_job.py`
- `results_pipeline.py`
- `mlb_model.py` (nuevo)
- `mlb_pipeline.py` (nuevo)
- `mlb_backtest.py` (nuevo)
- `test_mlb_model.py` (nuevo)
- `.github/workflows/elo_training.yml`
- `.github/workflows/market_snapshot.yml`
- `.github/workflows/elo_backtest.yml`

No crees manualmente `state/mlb/`. Los workflows generan y actualizan sus
archivos.

## Primera ejecución

1. Sube todos los archivos anteriores en un mismo commit.
2. Abre GitHub `Actions > Resultados y entrenamiento Elo Blindado`.
3. Pulsa `Run workflow`.
4. Activa `full_backfill` y ejecuta.
5. Espera a que termine; la descarga inicial de boxscores puede tardar.
6. Ejecuta `Snapshot de mercados Blindado` manualmente una vez.
7. En Streamlit reinicia la aplicación y pulsa `Actualizar datos deportivos`.

En el Panel de salud aparecerá `Modelo especializado MLB`, sus seis variables,
el Brier fuera de muestra, el baseline, el Brier de Elo y el motivo de
activación o bloqueo.

Que el entrenamiento termine correctamente no obliga al modelo a activarse.
Si no supera el test fuera de muestra, MLB seguirá bloqueada de manera
deliberada.
