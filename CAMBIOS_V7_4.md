# Cambios v7.4

- Retira descanso, forma, bullpen y carga del modelo MLB.
- Congela la especificación a Elo + abridor.
- Incrementa el schema especializado MLB de 1 a 2.
- Migra sin borrar los juegos históricos ni volver a descargar el backfill.
- Inicia una ventana prospectiva nueva de 100 partidos.
- Conserva todas las predicciones/IDs prospectivos para mantener idempotencia.
- Calcula métricas sobre los últimos 200 partidos prospectivos.
- Impide que un modelo schema 1 pueda activarse accidentalmente.
- Expone tipo, inicio y tamaño de la validación prospectiva en Streamlit.
