# Blindado v7.7.1 — observabilidad del matcher

## Hallazgo sobre `match_score=0.25`

No era un fallback. El score vigente suma `+0.25` cuando los inicios están a
90 minutos o menos. Los 38 resultados idénticos significan:

- componente de nombres: `0.0`;
- componente horario: `+0.25`;
- score total: `0.25`.

## Instrumentación añadida

Para cada descarte de liquidez, la auditoría conserva hasta los tres mejores
candidatos Bovada con:

- nombres Stake y Bovada, crudos y normalizados;
- horarios y diferencia absoluta en minutos;
- similitud directa e invertida, desglosada por participante;
- orientación elegida;
- componente nominal, componente horario y score total;
- selecciones principales crudas y normalizadas de ambas fuentes.

Esto permite distinguir ausencia real del evento, normalización defectuosa y
extracción incorrecta de outcomes sin relajar el umbral del matcher.

## Confianza: verificada, no modificada

La fórmula vigente queda expuesta en `configuracion_confianza` y cada descarte
por confianza incluye `desglose_confianza`. Se confirmó que actualmente es un
gate bloqueante aunque su docstring histórico decía que solo ordenaba. Esta
versión no decide todavía si debe dejar de bloquear ni cambia su ponderación.

## Histéresis: persistencia verificada

`activation_state` se guarda en `state/elo_state.json`, y el workflow de
entrenamiento incluye ese archivo en el commit. Una prueba nueva recarga tres
instancias independientes y confirma que `fail_streak` persiste.

`fail_streak` solo es el contador aplicable cuando el modelo está activo y se
evalúa su desactivación. Cuando está inactivo, el contador aplicable es
`pass_streak`. La cobertura ahora muestra `contador_en_uso` y `fase` para evitar
interpretar el `fail_streak` histórico de Cricket/Dota 2 como una falla de
persistencia.

## Sin cambios

- Frescura y snapshot schema 5.
- Umbrales o lógica de matching.
- Fórmula o umbral de confianza.
- Gates Brier/skill.
- Validación MLB 13/100.
- Frecuencia de workflows.
