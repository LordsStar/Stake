# Blindado v7.5 — gate estadístico y diagnóstico de descartes

## Qué corrige

- Sustituye el gate Brier `either` por `all`: el modelo debe aprobar a la vez
  el Brier absoluto y un Brier Skill Score mínimo de 2.5%.
- Cricket deja de activarse cuando tiene Brier absoluto aceptable pero skill
  negativo frente al baseline.
- Agrega histéresis persistente: activación con skill mínimo de 2.5%, salida
  bajo 1.5% y tres corridas de confirmación para evitar oscilaciones.
- Mantiene sin cambios la validación prospectiva MLB de 100 partidos nuevos.
- Divide el antiguo descarte global `sin_modelo` en causas accionables:
  `modelo_no_implementado`, `historial_insuficiente`, `modelo_no_aprobado`,
  `variables_prepartido_incompletas` y `estado_modelo_incompatible`.
- Agrega descartes por deporte y por namespace, además del detalle técnico de
  cada fallo de modelo.
- Agrega una reconciliación automática: descartados + eventos calificados debe
  coincidir exactamente con la cantidad de eventos de entrada.
- Actualiza la interfaz para presentar el nuevo diagnóstico por deporte.

## Qué mejora en la app

Esta versión no fabrica más picks. Su primera mejora es de seguridad y
observabilidad: permite distinguir cobertura histórica insuficiente de una
fuga real de variables en NBA, NFL, NHL, ATP, WTA o MLB. Solo después de medir
esa separación tiene sentido priorizar backfills, proveedores o frescura.

## Validación incluida

`test_model_diagnostics.py` comprueba que:

1. un Brier absoluto aceptable no oculta un skill negativo;
2. ambos criterios deben aprobarse para activar el modelo;
3. `sin_modelo` queda dividido y la auditoría reconcilia el 100% de eventos.

Ejecutar:

```bash
python -m unittest -v
```
