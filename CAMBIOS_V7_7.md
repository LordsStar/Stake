# Blindado v7.7 — splits de liquidez y gates finales

## Qué cambia

- Liquidez Bovada ahora se clasifica en orden determinístico:
  `bovada_sin_eventos_para_liga`, `bovada_evento_no_emparejado`,
  `bovada_match_score_bajo`, `bovada_seleccion_no_emparejada` y
  `bovada_observacion_vencida`.
- `bovada_observacion_vencida` reutiliza `market_freshness_status()` de v7.6;
  no existe un segundo criterio de frescura.
- El gate final se separa en:
  `seleccion_referencia_no_encontrada`, `cuota_fuera_de_rango`,
  `ev_menor_4`, `divergencia_mayor_9` y `confianza_menor_8`.
- La auditoría conserva por outcome cuota, probabilidades, EV, divergencia y
  confianza. EV y divergencia usan proporciones; confianza usa escala 0–10.
- La interfaz dice “coincidencia Bovada válida”, no “fresca”, y muestra tablas
  independientes para matching/liquidez y métricas de gates finales.

## Qué no cambia

- Snapshot schema 5 y la observabilidad v7.6.
- Gates Brier/skill e histéresis de v7.5.
- Validación prospectiva MLB: sigue bloqueada hasta 100 muestras.
- Umbrales de decisión: EV 4%, divergencia 9 puntos, confianza 8/10 y odds
  efectivas 1.40–2.00.

## Lectura de la siguiente corrida

Los conteos por deporte ya permiten cruzar ATP/WTA con las cinco causas de
liquidez. Los registros bajo `confianza_menor_8` permiten medir directamente
la frontera efectiva entre EV y confianza sin modificar la fórmula todavía.
