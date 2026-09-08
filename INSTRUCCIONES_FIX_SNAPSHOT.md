# Corrección del límite de antigüedad del snapshot

Reemplaza estos tres archivos juntos en la raíz del repositorio:

1. `app.py`
2. `blindado_core.py`
3. `cloud_snapshot_reader.py`

Después confirma el commit. Streamlit Cloud debe reiniciar la aplicación de
forma automática; si no lo hace, usa **Reboot app**.

## Qué cambia

- El límite global predeterminado pasa de 60 a 120 minutos.
- Entre 60 y 120 minutos el snapshot se carga, pero se muestra una advertencia.
- Los gates por evento no se relajan: continúan exigiendo una actualización de
  10, 30 o 120 minutos según la cercanía del comienzo.
- La referencia de Bovada continúa venciendo a los 120 minutos.
- El mensaje ya dirige al workflow de GitHub Actions, no a un fetcher local.

No hace falta volver a entrenar Elo por esta corrección. El archivo
`blindado_core.py` incluido usa Elo schema 4 y contiene las constantes
`BRIER_MULTICLASS_MAX` y `BRIER_GATE_MODE` requeridas por `app.py`.

Opcionalmente se puede fijar el límite en Streamlit Secrets:

```toml
SNAPSHOT_MAX_AGE_MINUTES = "120"
```

Si el snapshot supera 120 minutos, ejecuta manualmente el workflow de snapshot
en **GitHub > Actions** y revisa el log si no genera un commit nuevo.
