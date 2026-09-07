# Validación y uso — Blindado v6.2

## Qué usa como base

La app conserva Stake Sports Data API como ancla. De Stake obtiene los eventos,
participantes, fecha, estado, mercados, selecciones, cuota decimal y timestamp.
Una fuente externa nunca sustituye la cuota que se propone ejecutar.

## Por qué puede mostrar `NINGUNO`

El motor cuenta cada descarte y lo presenta en la interfaz. Las causas más
comunes son:

1. No existe moneyline o draw-no-bet en el evento de Stake.
2. El Elo no tiene cinco resultados por participante y ocho predicciones para
   esa competición, o su Brier es mayor que 0.23.
3. Bovada no respondió, no contiene el mismo evento o la referencia está vieja.
4. La cuota Stake está desactualizada o el evento ya comenzó.
5. En tenis/MMA/boxeo falta verificar estado físico.
6. El EV es menor que 4%, la divergencia supera 9 puntos o la confianza es
   menor que 8/10.
7. En fútbol hay riesgo de empate y Stake no ofrece DNB real.

El estado incluido fue reentrenado con 1,076 resultados. En esta reconstrucción
NFL obtuvo Brier 0.2359 y MLB 0.2516; ambos superan el máximo 0.23, por lo que
no están activos todavía. Esto es una decisión del gate, no un fallo al cargar
cuotas. Los workflows seguirán añadiendo resultados y recalculándolo.

## Qué cubre

- El snapshot solicita 16 slugs de Stake: fútbol, baloncesto, béisbol, hockey,
  fútbol americano, tenis, MMA, boxeo, cricket, rugby, voleibol, tenis de mesa,
  Counter-Strike, Dota 2, League of Legends y Valorant.
- Guarda todos los mercados activos devueltos por Stake.
- El motor de picks evalúa moneyline y DNB. Los demás mercados quedan visibles
  en la pestaña de eventos, pero no se modelan como si fueran equivalentes.
- Hay endpoint secundario Bovada para cada grupo solicitado, incluido un único
  endpoint de esports que después se empareja por participantes y horario.
- Resultados gratuitos: ESPN, TheSportsDB, Cricsheet y OpenDota.

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
