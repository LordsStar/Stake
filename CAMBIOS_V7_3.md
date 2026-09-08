# Cambios de Blindado v7.3

- Sustituye Elo puro por un modelo especializado únicamente para MLB.
- Reduce la entrada a seis señales agregadas para controlar colinealidad.
- Usa FIP reducido para abridor y bullpen; no introduce ERA/WHIP/K/BB como
  columnas independientes correlacionadas.
- Construye cada vector antes de aplicar el resultado del partido.
- Selecciona regularización en validación cronológica y mide el gate en test.
- Compara el nuevo modelo contra un baseline y contra el Elo MLB anterior.
- Obtiene calendario, abridores y boxscores desde los servicios públicos MLB.
- Calcula descanso, forma, bullpen y carga sin una API comercial.
- Registra alineaciones prospectivamente y las exige a <=90 minutos.
- No usa lesiones/alineaciones finales para fabricar un histórico retrospectivo.
- Transporta modelo y pregame MLB dentro de `snapshot.json`.
- Muestra las métricas especializadas MLB en el Panel de salud.
