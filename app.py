"""
app.py
=========================
UI de Streamlit para Blindado v6. Toda la lógica pesada vive en
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
    render_estado_snapshot(snapshot)
    age = snapshot_antiguedad_minutos(snapshot)
    if age > 60:
        raise ValueError("Snapshot vencido (>60 min). No se habilita el análisis.")
    stake_events, bovada_events = snapshot_a_normalized_events(snapshot)
    selected_set = set(selected)
    if "soccer" in selected_set:
        selected_set.add("football")
    if selected_set & core.ESPORTS_SLUGS:
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
    st.caption(f"Histórico Elo: {len(results)} resultados. Umbral de calibración Brier: ≤ {core.BRIER_MAX:.2f}.")
    if downloaded_markets:
        st.info(
            "Stake: se descargan y muestran todos los mercados activos devueltos por la API para los fixtures recibidos. "
            "Blindado usa para picks únicamente moneyline/DNB; totales, hándicaps y props no se evalúan todavía."
        )

    compatible = sum(1 for e in stake_events if core.pick_capability(e)[0])
    st.write(f"Eventos con conector Bovada configurado: **{compatible} / {len(stake_events)}**")

    with st.expander("Detalle de cobertura Elo por deporte"):
        if not coverage:
            st.info(
                "Todavía no hay ratings Elo guardados. Corre elo_trainer.py "
                "(local o vía GitHub Actions) después de cargar resultados "
                "en state/results/results.json."
            )
        for sport, info in coverage.items():
            icon = "✅" if info.get("modelo_activo") else "⛔"
            st.write(
                f"{icon} **{sport}**: {info['equipos_calibrados']} / {info['equipos_totales']} equipos con >= "
                f"{core.ELO_MIN_GAMES} partidos · Brier {info.get('brier')} ({info.get('predicciones_brier', 0)} predicciones)"
            )

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
        elo.state = {
            "schema_version": core.ELO_SCHEMA_VERSION,
            "ratings": {}, "brier": {}, "processed": {}, "draw_stats": {},
        }
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

    if aliases.data:
        with st.expander("Alias guardados"):
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
    st.set_page_config(page_title="Blindado v6 — Gated / Stake First", layout="wide")
    required_core = (
        "load_public_promotions", "pick_capability", "elo_namespace",
        "merge_movement_history", "export_private_state", "import_private_state",
    )
    missing_core = [name for name in required_core if not hasattr(core, name)]
    if missing_core:
        st.error(
            "Instalación incompleta: app.py es más nuevo que blindado_core.py. "
            "Reemplaza ambos archivos usando el mismo ZIP y reinicia la app. "
            f"Funciones ausentes: {', '.join(missing_core)}"
        )
        st.stop()
    st.title("🎯 Blindado v6 — Stake First, con Gates Obligatorios")
    st.caption(
        "Elo = único modelo estadístico válido · Bovada = solo referencia/liquidez · "
        "Stake = mercado ejecutable · ningún gate obligatorio se compensa con confianza alta"
    )

    core.init_promotions_file()

    with st.sidebar:
        st.header("Configuración")
        bankroll = st.number_input("Bankroll USD", min_value=1.0, value=100.0, step=10.0)
        stake_delay = st.number_input("Delay entre consultas Stake (seg)", min_value=0.0, value=0.0, step=0.05)
        stake_workers = st.slider("Consultas simultáneas Stake", min_value=1, max_value=10, value=6)
        bovada_enabled = st.checkbox("Usar Bovada como referencia", value=True)
        selected = st.multiselect("Deportes Stake", core.STAKE_SPORT_SLUGS, default=core.STAKE_SPORT_SLUGS)
        data_source = st.radio(
            "Fuente de datos", ["API oficial + respaldo automático", "Snapshot remoto"], index=0,
        )
        snapshot_repo = config_value("SNAPSHOT_REPO")
        snapshot_path = config_value("SNAPSHOT_PATH", "snapshot.json")
        snapshot_branch = config_value("SNAPSHOT_BRANCH", "main")

        st.divider()
        st.write("### Fuentes gratuitas")
        st.caption(
            "Resultados: ESPN + TheSportsDB + Cricsheet + OpenDota. "
            "Mercado secundario: Bovada. No se usa ninguna fuente de pago."
        )

    if st.button("🚀 Actualizar datos deportivos", type="primary"):
        try:
            movement: Dict[str, Any] = {}
            datos_de_snapshot = False
            if data_source == "Snapshot remoto":
                with st.spinner("Descargando snapshot verificado..."):
                    stake_events, bovada_events, no_disponibles, movement = load_remote_snapshot_fallback(
                        selected, snapshot_repo, snapshot_path, snapshot_branch
                    )
                datos_de_snapshot = True
            else:
                stake = core.StakeSportsDataCollector(
                    delay=float(stake_delay), max_workers=int(stake_workers),
                    api_key=config_value("STAKE_ODDS_API_KEY"),
                )
                bovada = core.BovadaCollector() if bovada_enabled else None
                try:
                    with st.spinner("Consultando la Sports Data API oficial de Stake..."):
                        stake_events = stake.fetch_all(selected)
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
                    datos_de_snapshot = True

            st.session_state["stake_events"] = core.dedupe_events(stake_events)
            st.session_state["bovada_events"] = core.dedupe_events(bovada_events if bovada_enabled else [])
            st.session_state["datos_de_snapshot"] = datos_de_snapshot
            if movement:
                core.merge_movement_history(movement)
            st.session_state["movement_history"] = core.append_movement_history(st.session_state["stake_events"])
            st.success(
                f"Cargados {len(st.session_state['stake_events'])} eventos Stake y "
                f"{len(st.session_state['bovada_events'])} referencias Bovada."
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
            if st.button("🧠 Ejecutar Blindado v6", type="primary"):
                candidates, audit = core.prepare_candidates(stake_events, bovada_events, promotions, float(bankroll))
                engine = core.BlindadoEngine(float(bankroll))
                pick = engine.choose_one(candidates)

                st.subheader("Resultado")
                if pick is None:
                    st.error("PICK DEL DÍA: NINGUNO")
                    st.write("Ningún evento superó simultáneamente todos los gates. Las causas exactas de esta ejecución son:")
                    labels = {
                        "sin_modelo": "Elo ausente o no calibrado",
                        "sin_mercado": "sin moneyline/DNB",
                        "frescura": "cuota desactualizada",
                        "liquidez": "sin coincidencia Bovada fresca",
                        "estado_fisico": "estado físico no verificado",
                        "deporte_o_liga_no_compatible": "deporte o liga sin circuito completo",
                        "ev_confianza_divergencia": "falló EV, confianza o divergencia",
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
        if st.session_state.get("datos_de_snapshot"):
            st.info(
                "Estos eventos vienen del snapshot remoto, no de la API en vivo. "
                "Desde v6.2.1 el snapshot solo guarda moneyline/draw_no_bet (las "
                "únicas claves que usa el motor de picks) para evitar que el "
                "archivo supere el límite de 25 MB. Totales, hándicaps y props "
                "completos solo se ven aquí en modo 'API oficial + respaldo automático'."
            )
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
