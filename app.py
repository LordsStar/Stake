"""
app.py
=========================
UI de Streamlit para Blindado v7.7.2 / snapshot schema 5 + MLB prospectivo. Toda la lógica pesada vive en
blindado_core.py (sin dependencia de Streamlit) — este archivo solo arma
la interfaz, botones y el flujo de datos.
"""

import os
import json
from typing import Any, Dict, List, Tuple

import streamlit as st

import blindado_core as core


# ============================================================
# Config helpers (Streamlit Secrets + variables de entorno)
# ============================================================
def config_value(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    return str(value or os.environ.get(name, default) or default)


def configured_snapshot_max_age_minutes() -> float:
    """Límite global del snapshot.

    Los gates de frescura por evento continúan siendo más estrictos
    (10/30/120 min según la cercanía del inicio). El límite global solo decide
    si el archivo puede cargarse para intentar el análisis.
    """
    try:
        configured = float(config_value("SNAPSHOT_MAX_AGE_MINUTES", "120"))
    except (TypeError, ValueError):
        configured = 120.0
    return max(60.0, min(configured, 240.0))


@st.cache_data(ttl=1800, show_spinner=False)
def discover_stake_sport_slugs(api_key: str = "") -> List[str]:
    """Una sola llamada a /sports; la lista fija es solo respaldo."""
    collector = core.StakeSportsDataCollector(api_key=api_key)
    return collector.available_sport_slugs()


def load_remote_snapshot_fallback(
    selected, snapshot_repo: str, snapshot_path: str, snapshot_branch: str
) -> Tuple[List[core.NormalizedEvent], List[core.NormalizedEvent], List[str], Dict[str, Any]]:
    """Se conserva igual que en v5.1: usa tu módulo cloud_snapshot_reader.py
    existente. No se tocó nada de ese contrato."""
    if not snapshot_repo:
        raise ValueError("No hay SNAPSHOT_REPO configurado para usar el respaldo.")
    from cloud_snapshot_reader import (
        obtener_snapshot_remoto,
        render_estado_snapshot,
        snapshot_a_normalized_events,
        snapshot_antiguedad_minutos,
    )

    snapshot = obtener_snapshot_remoto(
        snapshot_repo, snapshot_path, snapshot_branch,
        token=config_value("SNAPSHOT_GITHUB_TOKEN"),
    )
    max_age = configured_snapshot_max_age_minutes()
    render_estado_snapshot(snapshot, max_age_minutes=max_age)
    st.session_state["stake_coverage"] = snapshot.get("stake_coverage", {})
    st.session_state["mlb_model_payload"] = snapshot.get("mlb_model") or {}
    st.session_state["mlb_pregame"] = snapshot.get("mlb_pregame") or []
    age = snapshot_antiguedad_minutos(snapshot)
    if age > max_age:
        raise ValueError(
            f"Snapshot vencido (>{max_age:.0f} min). No se habilita el análisis."
        )
    stake_events, bovada_events = snapshot_a_normalized_events(snapshot)
    selected_set = set(selected)
    if "soccer" in selected_set:
        selected_set.add("football")
    if any(core.is_esport_slug(slug) for slug in selected_set):
        selected_set.add("esports")
    stake_events = [e for e in stake_events if e.sport in selected_set]
    movement = snapshot.get("movement_history")
    if not isinstance(movement, dict):
        legacy = snapshot.get("movimientos")
        movement = legacy if isinstance(legacy, dict) else {}
    return stake_events, bovada_events, snapshot.get("bovada_no_disponible", []), movement


# ============================================================
# Panel de salud
# ============================================================
def render_health_panel(
    stake_events: List[core.NormalizedEvent],
    bovada_events: List[core.NormalizedEvent],
    elo: "core.EloModel",
    physical_registry: "core.PhysicalStatusRegistry",
    promotions: List[Dict[str, Any]],
):
    st.subheader("🩺 Panel de salud")
    public_promotions = core.load_public_promotions(active_only=True)
    downloaded_markets = sum(len(e.markets) for e in stake_events)
    evaluable_markets = sum(
        1 for e in stake_events for m in e.markets if m.key in ("moneyline", "draw_no_bet")
    )
    cols = st.columns(6)
    cols[0].metric("Eventos Stake", len(stake_events))
    cols[1].metric("Eventos Bovada", len(bovada_events))
    cols[2].metric("Mercados descargados", downloaded_markets)
    cols[3].metric("Mercados evaluables", evaluable_markets, help="Solo moneyline y draw-no-bet entran al motor Blindado.")
    cols[4].metric("Promos públicas activas", len(public_promotions))
    cols[5].metric("Promos personales", sum(1 for p in promotions if p.get("confirmed_eligible")))

    coverage = elo.coverage()
    results = core.load_results()
    source_coverage = core.load_json(core.STATE_DIR / "source_coverage.json", {})
    pipeline_state = core.load_json(core.STATE_DIR / "results_pipeline_state.json", {})
    active_namespaces = sum(1 for info in coverage.values() if info.get("modelo_activo"))
    st.caption(
        f"Histórico Elo: {len(results)} resultados. Calibración: >= {core.BRIER_MIN} predicciones maduras, "
        f"ventana {core.BRIER_WINDOW}; exige simultáneamente Brier absoluto "
        f"(binario <= {core.BRIER_MAX:.3f}, 1X2 <= {core.BRIER_MULTICLASS_MAX:.3f}) "
        f"Y skill >= {core.BRIER_SKILL_MIN:.1%} frente al baseline. Histéresis: "
        f"no se desactiva hasta caer bajo {core.BRIER_SKILL_EXIT:.1%} durante "
        f"{core.BRIER_HYSTERESIS_RUNS} corridas con partidos nuevos en la ventana."
    )
    if downloaded_markets and downloaded_markets == evaluable_markets:
        st.info(
            "Modo snapshot: el archivo conserva únicamente moneyline/DNB, que son los mercados evaluados por Blindado. "
            "La recolección puede inspeccionar otros mercados, pero no se transportan en el snapshot para mantenerlo compacto."
        )
    elif downloaded_markets:
        st.info(
            "Consulta directa: se recibieron mercados adicionales, pero Blindado evalúa únicamente moneyline/DNB."
        )

    compatible = sum(1 for e in stake_events if core.pick_capability(e)[0])
    st.write(f"Eventos cuya categoría tiene ruta Bovada configurada: **{compatible} / {len(stake_events)}**")
    st.caption(
        "Una ruta configurada no garantiza que el evento y sus selecciones hayan sido emparejados; "
        "esa comprobación se realiza después de superar Elo."
    )

    if isinstance(source_coverage, dict) and source_coverage:
        processed = source_coverage.get("targets_processed", 0)
        total_targets = source_coverage.get("targets_in_snapshot", 0)
        st.write(
            f"Alimentación estadística: **{active_namespaces}** circuitos Elo activos · "
            f"última ronda procesó **{processed}/{total_targets}** ligas objetivo."
        )
        last_pipeline = pipeline_state.get("last_successful_run")
        if last_pipeline:
            st.caption(f"Último pipeline completo de resultados/Elo: {last_pipeline}")

    with st.expander("Detalle de cobertura Elo por deporte"):
        if not coverage:
            st.info(
                "Todavía no hay ratings Elo guardados. Corre elo_trainer.py "
                "(local o vía GitHub Actions) después de cargar resultados "
                "en state/results/results.json."
            )
        for sport, info in coverage.items():
            icon = "✅" if info.get("modelo_activo") else "⛔"
            skill = info.get("brier_skill_score")
            skill_label = "N/D" if skill is None else f"{skill:.1%}"
            st.write(
                f"{icon} **{sport}**: {info['equipos_calibrados']} / {info['equipos_totales']} equipos con >= "
                f"{core.ELO_MIN_GAMES} partidos · Brier {info.get('brier')} · baseline {info.get('baseline_brier')} · "
                f"skill {skill_label} · {info.get('predicciones_brier', 0)} predicciones maduras · "
                f"{info.get('motivo_calibracion')}"
            )

    with st.expander("Modelo especializado MLB"):
        try:
            from mlb_model import MLBPregameModel
            mlb = MLBPregameModel(st.session_state.get("mlb_model_payload"))
            info = mlb.coverage()
            icon = "✅" if info.get("modelo_activo") else "⛔"
            st.write(f"{icon} **MLB especializado**: {info.get('motivo')}")
            st.json({
                "muestras": info.get("muestras"),
                "muestras_test": info.get("muestras_test"),
                "muestras_prospectivas_totales": info.get("muestras_prospectivas_totales"),
                "metricas_fuera_de_muestra": info.get("metricas"),
                "variables_v2": info.get("variables"),
                "tipo_validacion": info.get("validacion"),
                "inicio_validacion_prospectiva": info.get("inicio_prospectivo"),
                "alineaciones_lesiones": "gates prospectivos; no entrenadas retroactivamente",
            })
        except Exception as exc:
            st.warning(f"Modelo MLB todavía no disponible: {exc}")

    with st.expander("Registros de estado físico (tenis/MMA/boxeo)"):
        st.write(f"Registros guardados: **{len(physical_registry.data)}**")
        vigentes = sum(1 for eid in physical_registry.data if physical_registry.is_verified_ok(eid)[0])
        st.write(f"Vigentes (<{core.PHYSICAL_STATUS_MAX_AGE_HOURS}h y confirmados): **{vigentes}**")


# ============================================================
# Gestor de estado físico
# ============================================================
def render_physical_status_manager(stake_events: List[core.NormalizedEvent], registry: "core.PhysicalStatusRegistry"):
    relevant = [e for e in stake_events if e.sport in core.PHYSICAL_STATUS_SPORTS]
    st.write(
        f"Eventos de tenis/MMA/boxeo cargados: **{len(relevant)}**. "
        "Sin un registro `verified_ok` vigente y confirmado, esos eventos "
        "se descartan automáticamente en Blindado, sin importar el EV."
    )
    if not relevant:
        st.info("No hay eventos de estos deportes en la carga actual.")
        return

    options = {f"{e.home} vs {e.away} ({e.league})": e for e in relevant}
    label = st.selectbox("Evento", list(options.keys()), key="phys_event_select")
    ev = options[label]

    existing = registry.get(ev.event_id)
    if existing:
        ok, reason = registry.is_verified_ok(ev.event_id)
        st.write(f"Registro actual: `{existing.get('status')}` — {'✅ vigente' if ok else f'⚠️ {reason}'}")

    srcs = core.FreeSourceRegistry().sources_for(ev)
    st.caption("Fuentes sugeridas para verificar estado físico:")
    for s in srcs:
        st.write(f"- {s}")

    with st.form(key="phys_status_form"):
        status = st.selectbox("Estado", ["verified_ok", "warning", "unknown"])
        source_url = st.text_input("URL de la fuente consultada")
        notes = st.text_area("Notas", value="")
        confirmed = st.checkbox("Confirmo que revisé la fuente personalmente")
        submitted = st.form_submit_button("Guardar registro")
        if submitted:
            if status == "verified_ok" and not source_url:
                st.error("verified_ok requiere una URL de fuente.")
            else:
                registry.upsert(ev.event_id, status, source_url, notes, confirmed)
                st.success("Registro guardado.")
                st.rerun()


# ============================================================
# Gestor de resultados (ingesta para entrenar Elo)
# ============================================================
def render_results_manager():
    st.write(
        "Sube resultados finalizados en CSV o JSON para entrenar el Elo. "
        "Columnas requeridas: " + ", ".join(core.REQUIRED_RESULT_FIELDS) + ". "
        "Nunca se rellenan campos faltantes — las filas inválidas se rechazan."
    )
    st.download_button(
        "Descargar plantilla CSV",
        data=(",".join(core.REQUIRED_RESULT_FIELDS) + ",source\n"
              "basketball,nba,manual:001,2026-09-01T23:00:00Z,Team A,Team B,101,98,final,manual\n"),
        file_name="resultados_plantilla.csv",
        mime="text/csv",
    )

    uploaded = st.file_uploader("Archivo de resultados", type=["csv", "json"])
    if uploaded is not None:
        try:
            if uploaded.name.endswith(".csv"):
                rows = core.parse_results_csv(uploaded.getvalue())
            else:
                import json as _json
                rows = _json.loads(uploaded.getvalue().decode("utf-8"))
                if isinstance(rows, dict):
                    rows = [rows]
        except Exception as exc:
            st.error(f"No se pudo leer el archivo: {exc}")
            rows = []

        if rows and st.button(f"Agregar {len(rows)} fila(s) al histórico"):
            added, errors = core.merge_results(rows)
            st.success(f"Agregados {added} resultado(s) nuevo(s).")
            if errors:
                with st.expander(f"{len(errors)} fila(s) rechazada(s)"):
                    for e in errors:
                        st.write(f"- {e}")

    st.divider()
    if st.button("🧮 Reentrenar Elo ahora con el histórico actual"):
        elo = core.EloModel()
        elo.state = core.fresh_elo_state(elo.state.get("activation_state", {}))
        aliases = core.TeamAliasRegistry()
        results = core.load_results()
        updated = core.train_elo_from_results(elo, aliases, results)
        st.success(f"Elo reconstruido con {updated} partido(s) en orden cronológico.")
        st.json(elo.coverage())


# ============================================================
# Gestor de alias de equipos
# ============================================================
def render_team_aliases_manager(aliases: "core.TeamAliasRegistry"):
    st.write(
        "No existe una base de datos gratuita completa de IDs de equipo. "
        "Este registro evita que variantes de nombre (ej. 'NY Yankees' vs "
        "'New York Yankees') se traten como equipos distintos en el Elo."
    )
    with st.form("alias_form"):
        sport = st.text_input("Namespace Elo (ej. 'baseball:mlb')")
        name = st.text_input("Nombre tal como aparece en Stake/resultados")
        canonical = st.text_input("ID canónico a usar (ej. 'new_york_yankees')")
        submitted = st.form_submit_button("Guardar alias")
        if submitted and sport and name and canonical:
            aliases.add_alias(sport, name, canonical)
            st.success("Alias guardado.")
            st.rerun()

    if aliases.automatic_data:
        with st.expander("Alias automáticos públicos"):
            st.json(aliases.automatic_data)
    if aliases.data:
        with st.expander("Alias manuales privados"):
            st.json(aliases.data)


def render_private_state_manager():
    st.write(
        "Streamlit Cloud usa disco efímero. Descarga este respaldo después de cambiar alias, "
        "promociones personales o estados físicos, y restáuralo si el contenedor reinicia. "
        "El archivo queda en tu dispositivo y no se publica en GitHub."
    )
    payload = json.dumps(core.export_private_state(), ensure_ascii=False, indent=2)
    st.download_button(
        "⬇️ Descargar respaldo privado",
        data=payload,
        file_name="blindado_estado_privado.json",
        mime="application/json",
    )
    uploaded = st.file_uploader("Restaurar respaldo privado", type=["json"], key="private_state_upload")
    if uploaded is not None and st.button("Restaurar ahora"):
        try:
            core.import_private_state(json.loads(uploaded.getvalue().decode("utf-8")))
            st.success("Estado privado restaurado correctamente.")
            st.rerun()
        except Exception as exc:
            st.error(f"No se pudo restaurar: {exc}")


def render_capability_matrix(stake_events: List[core.NormalizedEvent], elo: "core.EloModel"):
    rows = []
    seen = set()
    coverage = elo.coverage()
    for event in stake_events:
        key = (event.sport, event.league)
        if key in seen:
            continue
        seen.add(key)
        ok, reason = core.pick_capability(event)
        namespace = core.elo_namespace(event.sport, event.league)
        elo_info = coverage.get(namespace, {})
        rows.append({
            "deporte": event.sport,
            "liga": event.league,
            "namespace_elo": namespace,
            "conector_bovada": ok,
            "elo_activo": bool(elo_info.get("modelo_activo")),
            "diagnóstico": reason if not ok else (
                "circuito completo listo" if elo_info.get("modelo_activo")
                else "conector listo; falta cobertura/calibración Elo para esta competición"
            ),
        })
    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.info("Carga eventos para construir la matriz real de deportes y ligas recibidos.")


# ============================================================
# Gestor de promociones (esquema ampliado)
# ============================================================
def render_promotions_manager(promotions: List[Dict[str, Any]]):
    public_catalog = core.load_public_promotions()
    confirmations = st.session_state.setdefault("public_promo_confirmations", {})
    st.subheader("Catálogo público persistente")
    st.caption(
        "Proviene de state/public_promotions.json. Confirmar elegibilidad solo deja constancia en esta sesión: "
        "estas promociones públicas no alteran el EV porque la probabilidad de activar el beneficio no puede inventarse."
    )
    for p in public_catalog:
        status = core.public_promotion_status(p)
        with st.expander(f"{'✅' if status == 'activa' else '⚪'} {p['name']} — {status}"):
            st.write(p.get("summary", "Consulta los términos oficiales."))
            details = []
            if p.get("sport"):
                details.append(f"Deporte: {p['sport']}")
            if p.get("ends_on"):
                details.append(f"Vence: {p['ends_on']}")
            if p.get("min_bet_usd") is not None:
                details.append(f"Mínimo: USD {p['min_bet_usd']}")
            if p.get("max_benefit_usd") is not None:
                details.append(f"Máximo: USD {p['max_benefit_usd']}")
            st.write(" · ".join(details))
            st.link_button("Ver términos oficiales", p["terms_url"])
            confirmations[p["id"]] = st.checkbox(
                "Confirmo que aparece y aplica a mi cuenta",
                value=bool(confirmations.get(p["id"], False)),
                key=f"public_promo_{p['id']}",
                disabled=status != "activa",
            )
            if confirmations[p["id"]]:
                st.success("Elegibilidad confirmada por ti. Uso informativo; EV base sin ajuste automático.")

    st.divider()
    st.subheader("Promociones personales o cuantificables")
    st.caption(
        "Añade manualmente solo una promoción personal que veas en tu cuenta. Solo se aplica si confirmas elegibilidad "
        "y proporcionas sus parámetros; nunca se supone que una promoción sea universal."
    )
    with st.expander("➕ Añadir Odds Boost"):
        c1, c2 = st.columns(2)
        with c1:
            event_id = st.text_input("Event ID (opcional)", key="promo_event")
            sport = st.text_input("Deporte (opcional, slug)", key="promo_sport")
            market = st.text_input("Mercado exacto (opcional, ej. 'moneyline')", key="promo_market")
            selection = st.text_input("Selección (opcional)", key="promo_selection")
        with c2:
            boost = st.number_input("Boost %", min_value=0.0, value=10.0, step=0.5, key="promo_boost")
            min_bet = st.number_input("Apuesta mínima", min_value=0.0, value=0.0, step=1.0, key="promo_min")
            max_benefit = st.number_input("Beneficio máximo", min_value=0.0, value=0.0, step=1.0, key="promo_max")
            confirmed = st.checkbox("Confirmo que soy elegible para esta promoción", key="promo_confirm")
        if st.button("Guardar boost"):
            promotions.append({
                "enabled": True, "type": "odds_boost",
                "name": f"Stake Odds Boost +{boost}%",
                "event_id": event_id or None, "sport": sport or None,
                "market": market or None, "selection": selection or None,
                "boost_percent": boost,
                "min_bet": min_bet or None, "max_benefit": max_benefit or None,
                "bet_type": "single", "confirmed_eligible": confirmed,
            })
            core.save_promotions(promotions)
            st.success("Boost guardado." if confirmed else "Guardado, pero SIN 'confirmed_eligible' no se aplicará a ningún pick.")

    with st.expander("➕ Añadir Refund/Insurance"):
        c1, c2 = st.columns(2)
        with c1:
            r_event = st.text_input("Event ID", key="refund_event")
            r_sport = st.text_input("Deporte (slug)", key="refund_sport")
            r_market = st.text_input("Mercado exacto", key="refund_market")
            r_selection = st.text_input("Selección", key="refund_selection")
            r_name = st.text_input("Nombre", value="Stake Refund/Insurance", key="refund_name")
        with c2:
            r_trigger = st.number_input("Probabilidad del trigger de refund (0-1)", min_value=0.0, max_value=1.0, value=0.10, step=0.01, key="refund_trigger")
            r_fraction = st.number_input("Fracción del stake reembolsada", min_value=0.0, max_value=1.0, value=1.0, step=0.05, key="refund_fraction")
            r_confirmed = st.checkbox("Confirmo que soy elegible para esta promoción", key="refund_confirm")
        if st.button("Guardar refund"):
            promotions.append({
                "enabled": True, "type": "refund", "name": r_name,
                "event_id": r_event or None, "sport": r_sport or None,
                "market": r_market or None, "selection": r_selection or None,
                "trigger_probability": r_trigger, "refund_fraction": r_fraction,
                "bet_type": "single", "confirmed_eligible": r_confirmed,
            })
            core.save_promotions(promotions)
            st.success("Refund guardado." if r_confirmed else "Guardado, pero SIN 'confirmed_eligible' no se aplicará a ningún pick.")

    if promotions:
        with st.expander(f"Promociones guardadas ({len(promotions)})"):
            for i, p in enumerate(promotions):
                cols = st.columns([5, 1])
                cols[0].json(p)
                if cols[1].button("🗑️", key=f"del_promo_{i}"):
                    promotions.pop(i)
                    core.save_promotions(promotions)
                    st.rerun()


# ============================================================
# main()
# ============================================================
def main():
    st.set_page_config(page_title="Blindado v7.7.2 — Histéresis persistente", layout="wide")
    required_core = (
        "load_public_promotions", "pick_capability", "elo_namespace",
        "merge_movement_history", "export_private_state", "import_private_state",
        "BRIER_WINDOW", "BRIER_SKILL_MIN", "BRIER_SKILL_EXIT",
        "BRIER_HYSTERESIS_RUNS", "BRIER_MULTICLASS_MAX",
        "BRIER_GATE_MODE", "BRIER_ACTIVATION_POLICY_VERSION", "ELO_SCHEMA_VERSION",
        "is_esport_slug",
        "market_limit_minutes", "market_freshness_status",
        "summarize_market_observability", "fresh_elo_state",
    )
    missing_core = [name for name in required_core if not hasattr(core, name)]
    if missing_core:
        st.error(
            "Instalación incompleta: app.py es más nuevo que blindado_core.py. "
            "Reemplaza ambos archivos usando el mismo ZIP y reinicia la app. "
            f"Funciones ausentes: {', '.join(missing_core)}"
        )
        st.stop()
    st.title("🎯 Blindado v7.7.2 — Histéresis persistente")
    st.caption(
        "Elo o modelo MLB especializado = modelo independiente · Bovada = solo referencia/liquidez · "
        "Stake = mercado ejecutable · ningún gate obligatorio se compensa con confianza alta"
    )

    core.init_promotions_file()

    with st.sidebar:
        st.header("Configuración")
        bankroll = st.number_input("Bankroll USD", min_value=1.0, value=100.0, step=10.0)
        stake_delay = st.number_input("Delay entre consultas Stake (seg)", min_value=0.0, value=0.0, step=0.05)
        stake_workers = st.slider("Consultas simultáneas Stake", min_value=1, max_value=10, value=6)
        bovada_enabled = st.checkbox("Usar Bovada como referencia", value=True)
        snapshot_repo = config_value("SNAPSHOT_REPO")
        snapshot_path = config_value("SNAPSHOT_PATH", "snapshot.json")
        snapshot_branch = config_value("SNAPSHOT_BRANCH", "main")
        try:
            sport_options = discover_stake_sport_slugs(config_value("STAKE_ODDS_API_KEY"))
        except Exception:
            sport_options = list(core.STAKE_SPORT_SLUGS)
        selected = st.multiselect("Deportes Stake", sport_options, default=sport_options)
        source_options = ["Snapshot remoto", "API oficial + respaldo automático"] if snapshot_repo else ["API oficial + respaldo automático"]
        data_source = st.radio("Fuente de datos", source_options, index=0)
        if data_source.startswith("API oficial") and len(selected) > 5:
            st.warning(
                "La consulta directa recorre el catálogo completo y puede tardar varios minutos. "
                "Para todos los deportes usa el snapshot automático de GitHub Actions."
            )

        st.divider()
        st.write("### Fuentes gratuitas")
        st.caption(
            "Resultados: ESPN + TheSportsDB + Cricsheet + OpenDota. "
            "Mercado secundario: Bovada. No se usa ninguna fuente de pago."
        )

    if st.button("🚀 Actualizar datos deportivos", type="primary"):
        try:
            movement: Dict[str, Any] = {}
            if data_source == "Snapshot remoto":
                with st.spinner("Descargando snapshot verificado..."):
                    stake_events, bovada_events, no_disponibles, movement = load_remote_snapshot_fallback(
                        selected, snapshot_repo, snapshot_path, snapshot_branch
                    )
            else:
                stake = core.StakeSportsDataCollector(
                    delay=float(stake_delay), max_workers=int(stake_workers),
                    api_key=config_value("STAKE_ODDS_API_KEY"),
                )
                bovada = core.BovadaCollector() if bovada_enabled else None
                try:
                    with st.spinner("Consultando la Sports Data API oficial de Stake..."):
                        stake_events = stake.fetch_all(selected)
                    st.session_state["stake_coverage"] = stake.audit
                    if not stake_events:
                        raise RuntimeError("la API no devolvió eventos con mercados activos")
                    if stake.errors:
                        st.warning(f"Stake API: {len(stake.errors)} fixture(s) fallaron. Ejemplos: {stake.errors[:3]}")

                    bovada_events, no_disponibles = ([], [])
                    if bovada_enabled and bovada:
                        leagues = sorted({k for e in stake_events if (k := core.bovada_key_for_event(e))})
                        bovada_events, no_disponibles = bovada.fetch_all(leagues)
                        if no_disponibles and snapshot_repo:
                            st.warning(
                                "Bovada directo falló parcialmente. Intentando recuperar únicamente la referencia "
                                "Bovada desde el snapshot remoto."
                            )
                            try:
                                _, snap_bovada, snap_unavailable, snap_movement = load_remote_snapshot_fallback(
                                    selected, snapshot_repo, snapshot_path, snapshot_branch
                                )
                                bovada_events = core.dedupe_events(bovada_events + snap_bovada)
                                no_disponibles = snap_unavailable
                                movement = snap_movement
                            except Exception as snap_exc:
                                st.error(f"Fallback Bovada no disponible: {snap_exc}")
                    st.caption("Fuente utilizada: Stake Sports Data API oficial.")
                except Exception as api_exc:
                    if not snapshot_repo:
                        raise RuntimeError(
                            f"Falló la API oficial ({api_exc}) y no existe SNAPSHOT_REPO de respaldo."
                        ) from api_exc
                    st.warning(f"La API oficial falló ({api_exc}). Activando snapshot remoto de respaldo.")
                    with st.spinner("Cargando respaldo verificado..."):
                        stake_events, bovada_events, no_disponibles, movement = load_remote_snapshot_fallback(
                            selected, snapshot_repo, snapshot_path, snapshot_branch
                        )

            st.session_state["stake_events"] = core.dedupe_events(stake_events)
            st.session_state["bovada_events"] = core.dedupe_events(bovada_events if bovada_enabled else [])
            if movement:
                core.merge_movement_history(movement)
            st.session_state["movement_history"] = core.append_movement_history(st.session_state["stake_events"])
            st.success(
                f"Cargados {len(st.session_state['stake_events'])} eventos Stake y "
                f"{len(st.session_state['bovada_events'])} referencias Bovada."
            )
            coverage_run = st.session_state.get("stake_coverage", {})
            if coverage_run:
                st.info(
                    f"Catálogo revisado: {coverage_run.get('fixtures_listed', 0)} fixtures en "
                    f"{coverage_run.get('sports_requested', 0)} deportes; "
                    f"{coverage_run.get('fixtures_with_markets', 0)} quedaron pre-partido con mercados."
                )
            if no_disponibles:
                st.error(
                    f"Bovada no disponible para: {', '.join(no_disponibles)}. "
                    "Esas ligas quedan bloqueadas y no pueden producir pick."
                )
        except Exception as exc:
            st.session_state["stake_events"] = []
            st.session_state["bovada_events"] = []
            st.error(f"No fue posible actualizar los datos: {exc}")

    stake_events = st.session_state.get("stake_events", [])
    bovada_events = st.session_state.get("bovada_events", [])
    movement_history = st.session_state.get("movement_history", {})
    promotions = core.load_promotions()

    if "mlb_model_payload" not in st.session_state or "mlb_pregame" not in st.session_state:
        try:
            from mlb_model import MLB_MODEL_FILE, MLB_PREGAME_FILE
            st.session_state.setdefault("mlb_model_payload", core.load_json(MLB_MODEL_FILE, {}))
            st.session_state.setdefault("mlb_pregame", core.load_json(MLB_PREGAME_FILE, []))
        except Exception:
            st.session_state.setdefault("mlb_model_payload", {})
            st.session_state.setdefault("mlb_pregame", [])

    elo = core.EloModel()
    physical_registry = core.PhysicalStatusRegistry()
    aliases = core.TeamAliasRegistry()

    render_health_panel(stake_events, bovada_events, elo, physical_registry, promotions)

    tabs = st.tabs([
        "🏆 Ejecutar Blindado", "🩹 Estado físico", "📈 Resultados / Elo",
        "🏷️ Alias de equipos", "🎁 Promociones", "🔎 Eventos normalizados", "💾 Estado privado",
    ])

    with tabs[0]:
        if not stake_events:
            st.info("Carga eventos primero con el botón de arriba.")
        else:
            if st.button("🧠 Ejecutar Blindado v7.7.2", type="primary"):
                candidates, audit = core.prepare_candidates(
                    stake_events, bovada_events, promotions, float(bankroll),
                    st.session_state.get("mlb_model_payload"),
                    st.session_state.get("mlb_pregame", []),
                )
                engine = core.BlindadoEngine(float(bankroll))
                pick = engine.choose_one(candidates)

                st.subheader("Resultado")
                if pick is None:
                    st.error("PICK DEL DÍA: NINGUNO")
                    st.write("Ningún evento superó simultáneamente todos los gates. Las causas exactas de esta ejecución son:")
                    labels = {
                        "modelo_no_implementado": "modelo estadístico no implementado o no disponible",
                        "historial_insuficiente": "participantes o liga con historial insuficiente",
                        "modelo_no_aprobado": "modelo existente, pero todavía no aprobado por sus gates",
                        "variables_prepartido_incompletas": "modelo activo, pero faltan variables prepartido",
                        "estado_modelo_incompatible": "estado del modelo incompatible con el schema actual",
                        "sin_mercado": "sin moneyline/DNB",
                        "frescura": "cuota desactualizada",
                        "carried_forward_stale": "consulta fallida y última cuota válida vencida",
                        "market_observation_expired": "última observación exitosa vencida",
                        "liquidez": "sin coincidencia Bovada válida (compatibilidad)",
                        "bovada_sin_eventos_para_liga": "Bovada sin eventos para la liga",
                        "bovada_evento_no_emparejado": "evento Stake no emparejado con Bovada",
                        "bovada_match_score_bajo": "emparejamiento Bovada con score insuficiente",
                        "bovada_sin_mercado_principal": "evento Bovada sin moneyline/DNB utilizable",
                        "bovada_seleccion_no_emparejada": "selección Stake no emparejada con Bovada",
                        "bovada_observacion_vencida": "observación Bovada vencida",
                        "estado_fisico": "estado físico no verificado",
                        "deporte_o_liga_no_compatible": "deporte o liga sin circuito completo",
                        "ev_confianza_divergencia": "falló EV, confianza o divergencia (compatibilidad)",
                        "seleccion_referencia_no_encontrada": "selección de referencia no encontrada",
                        "cuota_fuera_de_rango": "cuota efectiva fuera de 1.40–2.00",
                        "ev_menor_4": "EV menor de 4%",
                        "divergencia_mayor_9": "divergencia mayor de 9 puntos",
                        "confianza_menor_8": "confianza menor de 8/10",
                        "en_vivo_o_sin_hora_valida": "en vivo, iniciado o sin hora válida",
                        "riesgo_empate_sin_dnb": "riesgo de empate sin DNB",
                    }
                    for reason, count in sorted(audit.get("descartes", {}).items(), key=lambda x: x[1], reverse=True):
                        st.write(f"- **{count}**: {labels.get(reason, reason)}")
                else:
                    st.success("PICK DEL DÍA: 1 selección")
                    st.json(core.candidate_report(pick, float(bankroll)))

                st.subheader("Auditoría (por qué se descartó cada evento)")
                st.json(audit)
                consistency = audit.get("consistencia_auditoria", {})
                if consistency and not consistency.get("cuadra"):
                    st.error("La auditoría no reconcilia todos los eventos de entrada; revisa el pipeline.")
                elif consistency:
                    st.success(
                        f"Auditoría reconciliada: {consistency.get('total_reconciliado')} / "
                        f"{consistency.get('total_entrada')} eventos."
                    )

                by_sport = audit.get("descartes_por_deporte", {})
                if by_sport:
                    st.subheader("Diagnóstico de descartes por deporte")
                    rows = []
                    for sport, reasons in sorted(by_sport.items()):
                        for reason, count in sorted(reasons.items(), key=lambda item: item[1], reverse=True):
                            rows.append({
                                "deporte": sport,
                                "motivo": labels.get(reason, reason),
                                "eventos": count,
                            })
                    st.dataframe(rows, use_container_width=True, hide_index=True)

                model_details = audit.get("detalle_modelo", {})
                if model_details:
                    with st.expander("Detalle técnico de modelos no disponibles"):
                        st.json(model_details)

                liquidity_details = audit.get("detalle_liquidez", [])
                if liquidity_details:
                    st.subheader("Diagnóstico de emparejamiento y liquidez Bovada")
                    st.caption(
                        "La observación vencida reutiliza exactamente la máquina de frescura v7.6; "
                        "las demás causas describen estructura o matching."
                    )
                    st.dataframe(liquidity_details, use_container_width=True, hide_index=True)

                final_gate_metrics = audit.get("metricas_descartes_finales", [])
                if final_gate_metrics:
                    st.subheader("Métricas crudas de descartes en gates finales")
                    st.caption(
                        "EV y divergencia se guardan como proporciones (0.04 = 4%); "
                        "confianza usa escala 0–10."
                    )
                    st.dataframe(final_gate_metrics, use_container_width=True, hide_index=True)

                observability = audit.get("observabilidad_mercados", {})
                provider_sports = observability.get("por_proveedor_deporte", {})
                if provider_sports:
                    st.subheader("Observabilidad y frescura por proveedor")
                    observation_rows = []
                    for provider, sports in sorted(provider_sports.items()):
                        for sport, statuses in sorted(sports.items()):
                            for status, count in sorted(statuses.items(), key=lambda item: item[1], reverse=True):
                                observation_rows.append({
                                    "proveedor": provider,
                                    "deporte": sport,
                                    "estado": status,
                                    "eventos": count,
                                    "bloqueante": status in {
                                        "carried_forward_stale", "market_observation_expired",
                                    },
                                })
                    st.dataframe(observation_rows, use_container_width=True, hide_index=True)

            if st.button("📋 Generar prompt Blindado para IA"):
                movements = core.movements_summary(movement_history)
                st.session_state["prompt"] = core.build_prompt(stake_events, movements)

        if "prompt" in st.session_state:
            st.subheader("Prompt listo")
            st.code(st.session_state["prompt"], language="text")

    with tabs[1]:
        render_physical_status_manager(stake_events, physical_registry)

    with tabs[2]:
        render_results_manager()
        st.divider()
        st.caption(
            "Para automatizar esto sin depender de tu computadora encendida, "
            "usa fetch_results_espn.py + fetch_results_free.py + elo_trainer.py dentro de un GitHub "
            "Action (ver .github/workflows/elo_training.yml incluido)."
        )

    with tabs[3]:
        render_team_aliases_manager(aliases)

    with tabs[4]:
        render_promotions_manager(promotions)

    with tabs[5]:
        st.write("**Matriz de capacidad real**")
        render_capability_matrix(stake_events, elo)
        st.divider()
        if stake_events:
            market_counts: Dict[str, int] = {}
            rows = []
            for e in stake_events:
                for m in e.markets:
                    market_counts[m.key] = market_counts.get(m.key, 0) + 1
                    rows.append({
                        "sport": e.sport, "league": e.league,
                        "event": f"{e.home} vs {e.away}", "market": m.name,
                        "outcomes": ", ".join(f"{o.selection}: {o.odds:.2f}" for o in m.outcomes),
                        "start": e.start_time,
                    })
            st.write("**Cobertura recibida por tipo de mercado**")
            st.dataframe(
                [{"market_key": key, "cantidad": value, "entra_al_pick": key in ("moneyline", "draw_no_bet")}
                 for key, value in sorted(market_counts.items(), key=lambda item: item[1], reverse=True)],
                use_container_width=True,
            )
            st.dataframe(rows, use_container_width=True)
        else:
            st.info("No hay eventos cargados.")

    with tabs[6]:
        render_private_state_manager()

    st.divider()
    st.caption(
        f"Blindado {core.APP_VERSION}. Los collectors dependen de interfaces públicas que pueden cambiar. "
        "No se utilizan técnicas de evasión de bloqueos ni automatización de apuestas."
    )


if __name__ == "__main__":
    main()
