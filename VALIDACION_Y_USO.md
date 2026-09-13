# Validación y uso — Blindado v7.8

## Qué usa como base

La app conserva Stake Sports Data API como ancla. De Stake obtiene los eventos,
participantes, fecha, estado, mercados, selecciones, cuota decimal y timestamp.
Una fuente externa nunca sustituye la cuota que se propone ejecutar.

## Por qué puede mostrar `NINGUNO`

El motor cuenta cada descarte y lo presenta en la interfaz. Las causas más
comunes son:

1. No existe moneyline o draw-no-bet en el evento de Stake.
2. El Elo no tiene cinco resultados por participante y 30 predicciones maduras
   para esa competición, o no supera simultáneamente Brier y skill del baseline.
3. Bovada no respondió, no contiene el mismo evento o la referencia está vieja.
4. La cuota Stake está desactualizada o el evento ya comenzó.
5. En tenis/MMA/boxeo falta verificar estado físico.
6. El EV es menor que 4%, la divergencia supera 9 puntos o la confianza es
   menor que 8/10.
7. En fútbol hay riesgo de empate y Stake no ofrece DNB real.

El estado incluido se reconstruye únicamente con resultados de procedencia
auditable. Las filas TheSportsDB anteriores a schema 6 fueron retiradas porque
no guardaban identidad de competición suficiente para validarlas.

## Qué cubre

- El snapshot descubre dinámicamente todos los deportes habilitados por Stake.
- Guarda moneyline y DNB, los únicos mercados que el motor actual modela.
- El motor de picks evalúa moneyline y DNB. Los demás mercados quedan visibles
  en la pestaña de eventos, pero no se modelan como si fueran equivalentes.
- Hay endpoint secundario Bovada para cada grupo solicitado, incluido un único
  endpoint de esports que después se empareja por participantes y horario.
- Resultados gratuitos: ESPN, TheSportsDB verificado, Cricsheet, OpenDota y
  Oracle's Elixir.

La cobertura es verificable por evento, no una promesa universal: una liga
ausente en las fuentes gratuitas quedará marcada como Elo no calibrado.

## Orden de instalación

1. Sube todo el contenido del ZIP a la raíz del repositorio, incluida `.github`.
2. Activa `Settings > Actions > General > Read and write permissions`.
3. Ejecuta manualmente `Snapshot de mercados Blindado`.
4. Cuando termine, ejecuta `Entrenamiento Elo Blindado`.
5. En Streamlit configura `app.py` como Main file path.
6. Agrega en Secrets:

```toml
SNAPSHOT_REPO = "LordsStar/Stake"
SNAPSHOT_PATH = "snapshot.json"
SNAPSHOT_BRANCH = "main"
```

No añadas usuario, contraseña, cookies, tier VIP ni token de GitHub al
repositorio. Al ser público, el snapshot se lee sin token.

## Promociones y estado privado

El catálogo público se versiona en `state/public_promotions.json`. La app exige
que confirmes si la promoción aparece en tu cuenta. Alias, estado físico,
promociones personales e historial local se descargan/restauran en la pestaña
`Estado privado`; no se comitean automáticamente a un repositorio público.
