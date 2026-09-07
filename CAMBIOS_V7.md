# Cambios verificados — Blindado v7

## Correcciones principales

- Stake deja de estar limitado a 16 slugs y 10 fixtures por deporte.
- Los deportes habilitados se descubren desde `GET /sports`.
- Cada deporte recorre categorías, torneos y la lista completa de fixtures
  por torneo según la documentación oficial.
- Solo se solicitan detalles de eventos prepartido, habilitados, no
  bloqueados y de tipo `match`; la auditoría cuenta todo lo excluido.
- El snapshot guarda la auditoría (`stake_coverage`) y el catálogo de deportes.
- Los esports nuevos se detectan dinámicamente y conservan namespaces Elo
  separados por videojuego.
- Bovada ya no puede cruzar por error dos videojuegos o dos deportes
  residuales solo porque coincidan nombres y horario.
- Se añadieron conectores secundarios gratuitos verificados para badminton,
  beach volley, darts, futsal, handball y snooker. Un endpoint vacío o caído
  sigue bloqueando el evento; no se fabrican cuotas.
- Se conserva Elo esquema 3: predicciones maduras, ventana 200, mínimo 30,
  Brier Skill Score mínimo 2% y tope binario 0.245.
- El workflow semanal ahora incluye el script `elo_backtest.py` que antes
  faltaba, y comparte el bloqueo de concurrencia con los demás workflows.
- La ingesta gratuita procesa todas las ligas del snapshot por defecto y escribe
  `state/source_coverage.json` con coincidencias y rechazos verificables.
- El catálogo público de promociones queda versionado en
  `state/public_promotions.json`; elegibilidad y promos personales continúan
  siendo confirmación manual y privada.

## Qué significa «cobertura completa»

El sistema enumera todos los deportes habilitados y todos los fixtures que el
árbol público de Sports Data API devuelve. No incluye casino, slots ni juegos
que no pertenezcan a esa API. Tampoco promete que cada evento produzca un pick:
para eso aún necesita moneyline/DNB, Elo calibrado, Bovada coincidente y fresca,
estado físico cuando aplique y los demás gates.

El snapshot es deliberadamente compacto: guarda moneyline/DNB de todos los
eventos prepartido utilizables. Para inspeccionar props, hándicaps y totales se
puede usar el modo de API directa, pero esos mercados no entran al modelo Elo.

## Instalación sin perder el histórico

1. Haz una copia de `state/results/results.json` y `state/elo_state.json`.
2. Sube el contenido de este paquete a la raíz del repositorio y permite
   reemplazar los archivos de código. No borres la carpeta `state` existente.
3. En GitHub ejecuta `Snapshot de mercados Blindado` una vez.
4. Después ejecuta `Entrenamiento Elo Blindado` una vez.
5. Comprueba que `snapshot.json` contiene `schema_version: 3` y
   `stake_coverage`, y que `state/elo_state.json` contiene `schema_version: 3`.
6. Reinicia la app en Streamlit Cloud. El archivo principal sigue siendo
   `app.py`.

El ZIP no incluye `snapshot.json`, `state/results/results.json` ni
`state/elo_state.json`, por lo que no sobrescribe tu histórico.
