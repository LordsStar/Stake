# Blindado v7.4 — corrección MLB prospectiva

## Por qué cambia el modelo

El backfill v1 produjo 1,499 observaciones y 200 partidos de test. El modelo de
seis variables obtuvo Brier 0.2559, peor que el baseline (0.2491) y que Elo
(0.2446). Por lo tanto, v1 quedó correctamente bloqueado.

La ablación diagnóstica mostró que descanso y forma eran inestables, mientras
que bullpen y carga no aportaban una mejora robusta. La especificación v2 queda
reducida y congelada a:

1. diferencia Elo con localía;
2. diferencia de calidad de los abridores mediante FIP reducido.

No se baja ningún umbral.

## Validación sin reutilizar el test inspeccionado

Como los 200 partidos anteriores ya fueron observados al escoger la nueva
especificación, no se usan otra vez para activarla. Al migrar desde schema 1:

- se conservan `state/mlb/games.json` y todos los boxscores;
- se entrena Elo+abridor con el histórico disponible;
- se fija `prospective_start_after` en el último partido conocido;
- el modelo comienza en `0/100 partidos nuevos`;
- los coeficientes y escalas quedan congelados durante la validación;
- cada workflow agrega predicciones antes de incorporar el resultado;
- los IDs se conservan para impedir duplicados.

Después de 100 partidos nuevos, se activa únicamente si:

- Brier <= 0.245;
- skill frente al baseline >= 2%;
- mejora frente a Elo >= 0.001.

Las métricas usan una ventana máxima de 200 partidos prospectivos recientes.
Si posteriormente se deterioran, el modelo vuelve a quedar inactivo.

## Alineaciones y lesiones

Continúan como información/gates prospectivos. No se incorporan como columnas
retroactivas porque no hay un histórico fiable de la hora exacta en que cada
cambio era conocido. A 90 minutos o menos se exigen ambas alineaciones.

## Archivos que debes reemplazar

- `app.py`
- `blindado_core.py`
- `mlb_model.py`
- `mlb_pipeline.py`
- `mlb_backtest.py`
- `test_mlb_model.py`

Los workflows incluidos no cambian su funcionamiento respecto del ZIP v7.3,
pero se incluyen para que el paquete sea autocontenido.

## Después de subirlos

1. Ejecuta `Resultados y entrenamiento Elo Blindado` normalmente. No necesitas
   repetir `full_backfill`: los 2,165 partidos ya guardados se conservan.
2. Ejecuta `Snapshot de mercados Blindado`.
3. Actualiza Streamlit.

El panel debe mostrar:

```text
variables_v2: [elo_diff, starter_fip_diff]
tipo_validacion: prospective_frozen_v2
validacion prospectiva: 0/100 partidos nuevos
```

Los workflows programados agregarán automáticamente los partidos nuevos. No se
debe ejecutar repetidamente el backfill para acelerar el contador: solamente
cuentan eventos posteriores al momento de congelación del modelo v2.
