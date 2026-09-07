"""
cloud_snapshot_reader.py — Este módulo SÍ corre en Streamlit Cloud.

Reemplaza las llamadas directas a StakeCollector/BovadaCollector dentro de
main() de app.py por una lectura del snapshot que
market_snapshot_job.py subió a GitHub. Streamlit Cloud nunca vuelve a
tocar stake.com ni bovada.lv directamente — evita el 403 de IP de
datacenter por diseño, no por reintentos.

Integración en app_stake_blindado.py:
  - Reemplaza el bloque `if st.button("🚀 Buscar todos los deportes en
    Stake", ...)` para que en vez de llamar `stake.fetch_all(...)` y
    `bovada.fetch_all(...)`, llame a `obtener_snapshot_remoto(...)` de
    este módulo y reconstruya los NormalizedEvent con
    `snapshot_a_normalized_events(...)`.
  - Agrega en la sidebar un campo para la URL raw de GitHub (o constrúyela
    desde repo/path/branch en secrets.toml).
"""

import time
from urllib.parse import quote
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import requests
import streamlit as st

from blindado_core import (
    NormalizedEvent,
    NormalizedMarket,
    NormalizedOutcome,
)


def construir_raw_url(repo: str, path: str, branch: str = "main") -> str:
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"


def obtener_snapshot_remoto(repo: str, path: str, branch: str = "main", timeout: int = 15, token: str = "") -> Dict[str, Any]:
    """
    Descarga el snapshot generado por market_snapshot_job.py. Se agrega un
    parámetro de cache-busting porque raw.githubusercontent.com cachea el
    contenido unos minutos en su CDN — sin esto, Streamlit Cloud podría
    seguir viendo una versión vieja justo después de que el fetcher local
    suba una nueva.
    """
    if token:
        safe_path = quote(path.strip("/"), safe="/")
        url = f"https://api.github.com/repos/{repo}/contents/{safe_path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        params = {"ref": branch, "_ts": int(time.time())}
    else:
        url = construir_raw_url(repo, path, branch)
        headers = {}
        params = {"_ts": int(time.time())}
    r = requests.get(url, params=params, headers=headers, timeout=timeout)
    if r.status_code == 404:
        raise FileNotFoundError(
            f"No se encontró {path} en {repo}@{branch}. "
            f"¿Ya ejecutaste el workflow 'Snapshot de mercados Blindado' al menos una vez?"
        )
    r.raise_for_status()
    if len(r.content) > 25_000_000:
        raise ValueError("El snapshot supera el límite de seguridad de 25 MB.")
    payload = r.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("stake_events", []), list):
        raise ValueError("El snapshot remoto no tiene una estructura válida.")
    return payload


def snapshot_antiguedad_minutos(snapshot: Dict[str, Any]) -> float:
    generado = snapshot.get("generado_utc")
    if not generado:
        return float("inf")
    try:
        dt = datetime.fromisoformat(generado.replace("Z", "+00:00"))
    except Exception:
        return float("inf")
    age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 60.0
    return float("inf") if age < -5 else max(0.0, age)


def _dict_a_normalized_event(d: Dict[str, Any]) -> NormalizedEvent:
    markets = [
        NormalizedMarket(
            key=m["key"],
            name=m["name"],
            outcomes=[NormalizedOutcome(**o) for o in m["outcomes"]],
        )
        for m in d.get("markets", [])
    ]
    return NormalizedEvent(
        event_id=d["event_id"],
        source=d["source"],
        sport=d["sport"],
        league=d["league"],
        home=d["home"],
        away=d["away"],
        start_time=d.get("start_time"),
        is_live=d.get("is_live", False),
        status=d.get("status", "scheduled"),
        last_update=d.get("last_update", ""),
        markets=markets,
        raw={},
    )


def snapshot_a_normalized_events(snapshot: Dict[str, Any]) -> Tuple[List[NormalizedEvent], List[NormalizedEvent]]:
    stake_events = [_dict_a_normalized_event(d) for d in snapshot.get("stake_events", [])]
    bovada_events = [_dict_a_normalized_event(d) for d in snapshot.get("bovada_events", [])]
    return stake_events, bovada_events


def render_estado_snapshot(snapshot: Dict[str, Any]) -> None:
    antiguedad = snapshot_antiguedad_minutos(snapshot)
    if antiguedad == float("inf"):
        st.error("⚠️ El snapshot no trae `generado_utc` — no se puede evaluar frescura.")
        return
    if antiguedad > 60:
        st.error(
            f"🔴 El snapshot tiene {antiguedad:.0f} minutos de antigüedad. "
            f"Verifica que el fetcher local siga corriendo en tu máquina/cron."
        )
    elif antiguedad > 25:
        st.warning(f"🟡 Snapshot con {antiguedad:.0f} minutos de antigüedad.")
    else:
        st.success(f"🟢 Snapshot fresco — {antiguedad:.0f} minutos de antigüedad.")

    if snapshot.get("bovada_no_disponible"):
        st.info(f"Bovada no disponible en la última corrida local para: {snapshot['bovada_no_disponible']}")
    coverage = snapshot.get("stake_coverage")
    if isinstance(coverage, dict):
        discovered = coverage.get("sports_discovered", 0)
        requested = coverage.get("sports_requested", 0)
        listed = coverage.get("fixtures_listed", 0)
        detailed = coverage.get("fixtures_with_markets", 0)
        st.caption(
            f"Cobertura oficial: {requested}/{discovered} deportes consultados · "
            f"{listed} fixtures catalogados · {detailed} eventos pre-partido con mercados."
        )
        limited = coverage.get("limited_fallback_sports") or []
        if limited:
            st.warning(
                "Cobertura parcial: no se pudo recorrer el catálogo completo de "
                + ", ".join(map(str, limited))
            )
