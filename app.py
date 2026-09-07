"""
app.py
=========================
UI de Streamlit para Blindado v6. Toda la lógica pesada vive en
blindado_core.py (sin dependencia de Streamlit) — este archivo solo arma
la interfaz, botones y el flujo de datos.
"""

import os
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
    movement = {"timestamp": snapshot.get("generado_utc"), "movements": snapshot.get("movimientos", [])}
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
    cols = st.columns(5)
    cols[0].metric("Eventos Stake", len(stake_events))
    cols[1].metric("Eventos Bovada", len(bovada_events))

    coverage = elo.coverage()
    equipos_calibrados = sum(v.get("equipos_calibrados", 0) for v in coverage.values())
    cols[2].metric("Equipos con Elo calibrado", equipos_calibrados)

    results = core.load_results()
    cols[3].metric("Resultados en histórico", len(results))
    cols[4].metric("Promociones confirmadas", sum(1 for p in promotions if p.get("confirmed_eligible")))

    with st.expander("Detalle de cobertura Elo por deporte"):
        if not coverage:
            st.info(
                "Todavía no hay ratings Elo guardados. Corre elo_trainer.py "
                "(local o vía GitHub Actions) después de cargar resultados "
                "en state/results/results.json."
            )
        for sport, info in coverage.items():
            st.write(f"**{sport}**: {info['equipos_calibrados']} / {info['equipos_totales']} equipos calibrados (>= {core.ELO_MIN_GAMES} partidos)")

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
              "basketball,manual:001,2026-09-01T23:00:00Z,Team A,Team B,101,98,final,manual\n"),
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
        aliases = core.TeamAliasRegistry()
        results = core.load_results()
        updated = core.train_elo_from_results(elo, aliases, results)
        st.success(f"Elo actualizado con {updated} partido(s) nuevo(s) (orden cronológico).")
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
        sport = st.text_input("Deporte (slug, ej. 'baseball')")
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


# ============================================================
# Gestor de promociones (esquema ampliado)
# ============================================================
def render_promotions_manager(promotions: List[Dict[str, Any]]):
    st.caption(
        "Una promoción SOLO se aplica si marcas 'confirmed_eligible' — nunca "
        "se asume elegibilidad universal (Regla 9)."
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
        st.write(", ".join(sorted(set(core.FREE_SOURCES.keys()) | set(core.ESPORT_SOURCES.keys()))))

    if st.button("🚀 Actualizar datos deportivos", type="primary"):
        try:
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
                    if not stake_events:
                        raise RuntimeError("la API no devolvió eventos con mercados activos")
                    if stake.errors:
                        st.warning(f"Stake API: {len(stake.errors)} fixture(s) fallaron. Ejemplos: {stake.errors[:3]}")

                    bovada_events, no_disponibles = ([], [])
                    if bovada_enabled and bovada:
                        leagues = sorted({k for e in stake_events if (k := core.bovada_key_for_event(e))})
                        bovada_events, no_disponibles = bovada.fetch_all(leagues)
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
            st.session_state["movement_history"] = core.append_movement_history(st.session_state["stake_events"])
            st.success(
                f"Cargados {len(st.session_state['stake_events'])} eventos Stake y "
                f"{len(st.session_state['bovada_events'])} referencias Bovada."
            )
            if no_disponibles:
                st.warning(f"Bovada no disponible para: {', '.join(no_disponibles)}")
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
        "🏷️ Alias de equipos", "🎁 Promociones", "🔎 Eventos normalizados",
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
                    st.write("Ningún evento superó simultáneamente TODOS los gates obligatorios (modelo, frescura, liquidez, estado físico si aplica) más EV/confianza/divergencia.")
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
            "usa fetch_results_espn.py + elo_trainer.py dentro de un GitHub "
            "Action (ver .github/workflows/elo_training.yml incluido)."
        )

    with tabs[3]:
        render_team_aliases_manager(aliases)

    with tabs[4]:
        render_promotions_manager(promotions)

    with tabs[5]:
        if stake_events:
            rows = []
            for e in stake_events:
                for m in e.markets:
                    rows.append({
                        "sport": e.sport, "league": e.league,
                        "event": f"{e.home} vs {e.away}", "market": m.name,
                        "outcomes": ", ".join(f"{o.selection}: {o.odds:.2f}" for o in m.outcomes),
                        "start": e.start_time,
                    })
            st.dataframe(rows, use_container_width=True)
        else:
            st.info("No hay eventos cargados.")

    st.divider()
    st.caption(
        f"Blindado {core.APP_VERSION}. Los collectors dependen de interfaces públicas que pueden cambiar. "
        "No se utilizan técnicas de evasión de bloqueos ni automatización de apuestas."
    )


if __name__ == "__main__":
    main()
