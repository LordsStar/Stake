# Blindado v7.8 — identidad segura de competiciones

## Corrección crítica

- Se eliminó la selección automática de ligas TheSportsDB mediante similitud
  difusa. Solo se admite un mapping con `verified: true` o una coincidencia
  textual exacta, única y compatible con la categoría/país.
- El snapshot schema 6 conserva categoría, IDs y `competition_key` de Stake.
- Elo schema 6 utiliza esa identidad para no mezclar ligas homónimas.
- Cada fila TheSportsDB exige ID/nombre de liga origen y método de mapping.
- La migración retiró todas las filas TheSportsDB antiguas sin procedencia y
  reconstruyó Elo desde fuentes que sí pueden auditarse.

## Operación y pruebas

- `merge_results()` permite correcciones auditadas en vez de ignorar IDs ya
  existentes.
- La cadencia esperada del snapshot queda fijada en 30 minutos; los retrasos
  reales se reportan por separado.
- El lector puro de snapshots ya no importa Streamlit al cargar el módulo.
- GitHub Actions ejecuta la suite completa en pushes y pull requests.
- Al publicar esta migración, el workflow de Elo se dispara una vez, retira el
  histórico inseguro y publica el estado reconstruido; sus commits de estado
  no vuelven a disparar el workflow.

Stake continúa siendo la fuente principal de eventos, mercados y cuotas.
Bovada continúa siendo referencia secundaria. Las demás fuentes gratuitas
solo aportan entrenamiento o validación y nunca fabrican un pick.
