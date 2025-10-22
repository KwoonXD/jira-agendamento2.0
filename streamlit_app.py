# streamlit_app.py
# ----------------------------------------------------
# Painel Field Service – versão compatível com sua base já funcional
# Mantém: JiraAPI.buscar_chamados_enhanced, whoami, debug sidebar, expandidos por loja,
# heatmap gratuito via Nominatim, filtros e KPIs, e transições (inclui TEC-CAMPO).

import io
import csv
import os
import time
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import pandas as pd
import streamlit as st

# ==== Config da página ====
st.set_page_config(
    page_title="Painel Field Service",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==== Auto‐refresh com fallback ====
if "auto_refresh_on" not in st.session_state:
    st.session_state.auto_refresh_on = True

try:
    from streamlit_autorefresh import st_autorefresh
    if st.session_state.auto_refresh_on:
        st_autorefresh(interval=90_000, key="auto_refresh_90s")
except Exception:
    # Fallback por meta refresh (caso o componente não esteja disponível no deploy)
    if st.session_state.auto_refresh_on:
        st.markdown('<meta http-equiv="refresh" content="90">', unsafe_allow_html=True)
        st.caption("⏱️ Auto-refresh por fallback (meta refresh).")

# ==== Estado básico ====
if "history" not in st.session_state:
    st.session_state.history = []
if "filters" not in st.session_state:
    st.session_state.filters = {
        "threshold": 2,
        "uf": "",
        "q": "",
        "days": 14,
        "statuses": ["AGENDAMENTO", "Agendado", "TEC-CAMPO"],
    }
if "presets" not in st.session_state:
    st.session_state.presets = {}

# ==== Imports da sua base util ====
from utils.jira_api import JiraAPI
from utils.messages import gerar_mensagem, verificar_duplicidade

# ==== Credenciais (secrets) ====
EMAIL = st.secrets.get("EMAIL", "")
API_TOKEN = st.secrets.get("API_TOKEN", "")
CLOUD_ID = st.secrets.get("CLOUD_ID")
USE_EX_API = str(st.secrets.get("USE_EX_API", "true")).lower() == "true"

if not EMAIL or not API_TOKEN:
    st.error("⚠️ Configure `EMAIL` e `API_TOKEN` em `.streamlit/secrets.toml`.")
    st.stop()
if USE_EX_API and not CLOUD_ID:
    st.error("⚠️ `USE_EX_API=true`, mas faltou `CLOUD_ID` em secrets.")
    st.stop()

jira = JiraAPI(
    EMAIL,
    API_TOKEN,
    "https://delfia.atlassian.net",
    use_ex_api=USE_EX_API,
    cloud_id=CLOUD_ID,
)

# ==== Autenticação rápida ====
who, dbg_who = jira.whoami()
if not who:
    st.error(
        "❌ Falha de autenticação no Jira.\n\n"
        f"- URL: `{dbg_who.get('url')}`\n"
        f"- Status: `{dbg_who.get('status')}`\n"
        f"- Erro: `{dbg_who.get('error')}`"
    )
    st.stop()

# ==== Campos a buscar ====
FIELDS = (
    "summary,customfield_14954,customfield_14829,customfield_14825,"
    "customfield_12374,customfield_12271,customfield_11993,"
    "customfield_11994,customfield_11948,customfield_12036,customfield_12279,"
    "status,created,resolutiondate,updated"
)

# ==== JQLs (mantidos) ====
JQL_PEND = 'project = FSA AND status = "AGENDAMENTO" ORDER BY updated DESC'
JQL_AG   = 'project = FSA AND status = "Agendado" ORDER BY updated DESC'
JQL_TC   = 'project = FSA AND status = "TEC-CAMPO" ORDER BY updated DESC'

# IDs confirmados para visão combinada (não mude se já conferiu)
STATUS_ID_AGENDAMENTO = 11499
STATUS_ID_AGENDADO    = 11481
STATUS_ID_TEC_CAMPO   = 11500
JQL_COMBINADA = (
    f"project = FSA AND status in ({STATUS_ID_AGENDAMENTO},{STATUS_ID_AGENDADO},{STATUS_ID_TEC_CAMPO})"
)

# Resolvidos para o gráfico
JQL_RESOLVIDOS_BASE = (
    'project = FSA AND status in (11498, 10702, "Encerrado", "Resolvido") '
    'AND resolutiondate >= "{from_iso}" AND resolutiondate <= "{to_iso}"'
)

# ==== Helpers de parsing ====
def parse_dt(dt_str: str):
    if not dt_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(dt_str, fmt).astimezone(timezone.utc)
        except Exception:
            pass
    return None

def loja_from_issue(issue):
    f = issue.get("fields", {}) or {}
    return (f.get("customfield_14954") or {}).get("value") or "Loja Desconhecida"

def cidade_from_issue(issue):
    return (issue.get("fields", {}) or {}).get("customfield_11994") or ""

def uf_from_issue(issue):
    return ((issue.get("fields", {}) or {}).get("customfield_11948") or {}).get("value") or ""

def cep_from_issue(issue):
    return (issue.get("fields", {}) or {}).get("customfield_11993") or ""

def endereco_from_issue(issue):
    return (issue.get("fields", {}) or {}).get("customfield_12271") or ""

def updated_from_issue(issue):
    return parse_dt((issue.get("fields", {}) or {}).get("updated"))

def created_from_issue(issue):
    return parse_dt((issue.get("fields", {}) or {}).get("created"))

def resolutiondate_from_issue(issue):
    return parse_dt((issue.get("fields", {}) or {}).get("resolutiondate"))

def is_loja_critica(loja_data):
    qtd = loja_data.get("qtd", 0)
    last_upd = loja_data.get("last_updated")
    stale = False
    if last_upd:
        stale = (datetime.now(timezone.utc) - last_upd) > timedelta(days=7)
    return (qtd >= 5) or stale

# ==== Buscas (mantidas com seu Enhanced) ====
pendentes_raw, dbg_pend = jira.buscar_chamados_enhanced(JQL_PEND, FIELDS, page_size=200)
agendados_raw, dbg_ag   = jira.buscar_chamados_enhanced(JQL_AG,   FIELDS, page_size=200)
tec_raw,      dbg_tc    = jira.buscar_chamados_enhanced(JQL_TC,   FIELDS, page_size=300)
combo_raw,    dbg_combo = jira.buscar_chamados_enhanced(JQL_COMBINADA, FIELDS, page_size=600)

# Janela para tendência
days_window = int(st.session_state.filters["days"])
to_dt = datetime.now(timezone.utc)
from_dt = to_dt - timedelta(days=days_window)
jql_res = JQL_RESOLVIDOS_BASE.format(
    from_iso=from_dt.strftime("%Y-%m-%d %H:%M"),
    to_iso=to_dt.strftime("%Y-%m-%d %H:%M")
)
resolvidos_raw, dbg_res = jira.buscar_chamados_enhanced(jql_res, FIELDS, page_size=600)

# ==== Agrupamentos ====
agrup_pend = jira.agrupar_chamados(pendentes_raw)

grouped_sched = defaultdict(lambda: defaultdict(list))
if agendados_raw:
    for issue in agendados_raw:
        f = issue.get("fields", {}) or {}
        loja = (f.get("customfield_14954") or {}).get("value") or "Loja Desconhecida"
        raw_dt = f.get("customfield_12036")
        data_str = "Não definida"
        if raw_dt:
            try:
                data_str = datetime.strptime(raw_dt, "%Y-%m-%dT%H:%M:%S.%f%z").strftime("%d/%m/%Y")
            except Exception:
                data_str = str(raw_dt)
        grouped_sched[data_str][loja].append(issue)

agrup_tec = jira.agrupar_chamados(tec_raw)

raw_by_loja = defaultdict(list)
for i in (pendentes_raw or []) + (agendados_raw or []) + (tec_raw or []):
    raw_by_loja[loja_from_issue(i)].append(i)

# ==== Construções de visão geral / destaques ====
kpi = {"AGENDAMENTO": 0, "Agendado": 0, "TEC-CAMPO": 0}
for issue in combo_raw or []:
    fields = issue.get("fields") or {}
    status_name = (fields.get("status") or {}).get("name")
    if status_name in kpi:
        kpi[status_name] += 1

contagem_por_loja = {}
for issue in combo_raw or []:
    loja = loja_from_issue(issue)
    cidade = cidade_from_issue(issue)
    uf = uf_from_issue(issue)
    upd = updated_from_issue(issue)
    if loja not in contagem_por_loja:
        contagem_por_loja[loja] = {
            "cidade": cidade, "uf": uf, "qtd": 0, "last_updated": upd,
            "endereco": endereco_from_issue(issue), "cep": cep_from_issue(issue)
        }
    contagem_por_loja[loja]["qtd"] += 1
    if cidade and not contagem_por_loja[loja]["cidade"]:
        contagem_por_loja[loja]["cidade"] = cidade
    if uf and not contagem_por_loja[loja]["uf"]:
        contagem_por_loja[loja]["uf"] = uf
    if upd and (contagem_por_loja[loja]["last_updated"] is None or upd > contagem_por_loja[loja]["last_updated"]):
        contagem_por_loja[loja]["last_updated"] = upd
    if not contagem_por_loja[loja]["endereco"] and endereco_from_issue(issue):
        contagem_por_loja[loja]["endereco"] = endereco_from_issue(issue)
    if not contagem_por_loja[loja]["cep"] and cep_from_issue(issue):
        contagem_por_loja[loja]["cep"] = cep_from_issue(issue)

top_list = sorted(
    [
        {
            "loja": loja,
            "cidade": data["cidade"],
            "uf": data["uf"],
            "qtd": data["qtd"],
            "last_updated": data["last_updated"],
            "critica": is_loja_critica(data),
        }
        for loja, data in contagem_por_loja.items()
    ],
    key=lambda x: (-x["qtd"], x["loja"])
)[:5]

# ==== Sidebar – Ações + Debug ====
with st.sidebar:
    st.header("Ações")
    if st.button("↩️ Desfazer última ação"):
        if st.session_state.history:
            action = st.session_state.history.pop()
            reverted = 0
            for key in action["keys"]:
                trans = jira.get_transitions(key)
                rev_id = next((t["id"] for t in trans if (t.get("to", {}) or {}).get("name") == action["from"]), None)
                if rev_id and jira.transicionar_status(key, rev_id).status_code == 204:
                    reverted += 1
            st.success(f"Revertido: {reverted} FSAs → {action['from']}")
        else:
            st.info("Nenhuma ação para desfazer.")

    st.markdown("---")
    st.header("Transição de Chamados")

    lojas_pend = set(agrup_pend.keys())
    lojas_ag = set()
    if grouped_sched:
        for _, stores in grouped_sched.items():
            lojas_ag |= set(stores.keys())
    lojas_tc = set(agrup_tec.keys())
    lojas_cat = ["—"] + sorted(lojas_pend | lojas_ag | lojas_tc)

    loja_sel = st.selectbox("Selecione a loja:", lojas_cat, help="Usado nas ações em massa abaixo.")

    with st.expander("🛠️ Debug (Enhanced Search)"):
        st.json({
            "use_ex_api": USE_EX_API, "cloud_id": CLOUD_ID,
            "pendentes": {"count": len(pendentes_raw or []), **dbg_pend},
            "agendados": {"count": len(agendados_raw or []), **dbg_ag},
            "tec_campo": {"count": len(tec_raw or []), **dbg_tc},
            "combo": {"count": len(combo_raw or []), **dbg_combo},
            "resolvidos": {"count": len(resolvidos_raw or []), **dbg_res},
            "last_call": {
                "url": getattr(jira, "last_url", None),
                "method": getattr(jira, "last_method", None),
                "status": getattr(jira, "last_status", None),
                "count": getattr(jira, "last_count", None),
                "params": getattr(jira, "last_params", None),
                "error": getattr(jira, "last_error", None),
            }
        })

    if loja_sel != "—":
        st.markdown("### 🚚 Fluxo rápido")
        em_campo = st.checkbox("Técnico em campo? (agendar + mover tudo → Tec-Campo)")

        if em_campo:
            st.caption("Preencha os dados do agendamento:")
            data = st.date_input("Data")
            hora = st.time_input("Hora")
            tecnico = st.text_input("Técnicos (Nome-CPF-RG-TEL)")

            dt_iso = datetime.combine(data, hora).strftime("%Y-%m-%dT%H:%M:%S.000-0300")
            extra_ag = {"customfield_12036": dt_iso}
            if tecnico:
                extra_ag["customfield_12279"] = {
                    "type": "doc", "version": 1,
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": tecnico}]}],
                }

            keys_pend  = [i["key"] for i in (pendentes_raw or []) if loja_from_issue(i) == loja_sel]
            keys_sched = [i["key"] for i in (agendados_raw or [])  if loja_from_issue(i) == loja_sel]
            all_keys = keys_pend + keys_sched

            if st.button(f"Agendar e mover {len(all_keys)} FSAs → Tec-Campo"):
                errors, moved = [], 0

                # 1) Agendar pendentes
                for k in keys_pend:
                    trans = jira.get_transitions(k)
                    agid = next((t["id"] for t in trans if "agend" in t["name"].lower()), None)
                    if agid:
                        r = jira.transicionar_status(k, agid, fields=extra_ag)
                        if r.status_code != 204:
                            errors.append(f"{k}⏳{r.status_code}")

                # 2) Mover todos para Tec-Campo
                for k in all_keys:
                    trans = jira.get_transitions(k)
                    tcid = next((t["id"] for t in trans if "tec-campo" in (t.get("to", {}) or {}).get("name", "").lower()), None)
                    if tcid:
                        r = jira.transicionar_status(k, tcid)
                        if r.status_code == 204:
                            moved += 1
                        else:
                            errors.append(f"{k}➡️{r.status_code}")

                if errors:
                    st.error("Erros:")
                    [st.code(e) for e in errors]
                else:
                    st.success(f"{len(all_keys)} FSAs agendados e movidos → Tec-Campo")
                    st.session_state.history.append({"keys": all_keys, "from": "AGENDADO"})

        else:
            # fluxo manual
            opts = (
                [i["key"] for i in (pendentes_raw or []) if loja_from_issue(i) == loja_sel] +
                [i["key"] for i in (agendados_raw  or []) if loja_from_issue(i) == loja_sel] +
                [i["key"] for i in (tec_raw       or []) if loja_from_issue(i) == loja_sel]
            )
            sel = st.multiselect("FSAs (pend.+agend.+tec-campo):", sorted(set(opts)))
            if sel:
                trans_opts = {t["name"]: t["id"] for t in jira.get_transitions(sel[0])}
                choice = st.selectbox("Transição:", ["—"] + list(trans_opts))
                extra = {}
                if choice and "agend" in choice.lower():
                    d = st.date_input("Data")
                    h = st.time_input("Hora")
                    tec = st.text_input("Técnicos (Nome-CPF-RG-TEL)")
                    iso = datetime.combine(d, h).strftime("%Y-%m-%dT%H:%M:%S.000-0300")
                    extra["customfield_12036"] = iso
                    if tec:
                        extra["customfield_12279"] = {
                            "type": "doc", "version": 1,
                            "content": [{"type": "paragraph", "content": [{"type": "text", "text": tec}]}],
                        }
                if st.button("Aplicar"):
                    if choice in (None, "—") or not sel:
                        st.warning("Selecione FSAs e transição.")
                    else:
                        prev_issue = jira.get_issue(sel[0]) or {}
                        prev = ((prev_issue.get("fields") or {}).get("status") or {}).get("name", "AGENDADO")
                        errs, mv = [], 0
                        for k in sel:
                            r = jira.transicionar_status(k, trans_opts[choice], fields=extra or None)
                            if r.status_code == 204:
                                mv += 1
                            else:
                                errs.append(f"{k}:{r.status_code}")
                        if errs:
                            st.error("Falhas:")
                            [st.code(e) for e in errs]
                        else:
                            st.success(f"{mv} FSAs movidos → {choice}")
                            st.session_state.history.append({"keys": sel, "from": prev})

# ==== Título ====
st.title("📱 Painel Field Service")

# ==== Abas ====
tab_details, tab_overview = st.tabs(["📋 Chamados", "📊 Visão Geral"])

# ============================
# 📋 Chamados (Detalhes)
# ============================
with tab_details:
    # Destaques colapsáveis (N+)
    st.subheader("🏷️ Lojas com N+ chamados (AGENDAMENTO • Agendado • TEC-CAMPO)")
    with st.expander("Abrir/Fechar destaques", expanded=False):
        c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
        threshold = c1.number_input("Mín. chamados", min_value=2, max_value=50, value=int(st.session_state.filters["threshold"]), step=1)
        order_opt = c2.selectbox("Ordenar por", ["Chamados ↓", "Loja ↑", "Cidade ↑"])
        uf_filter = c3.text_input("Filtrar UF", value=st.session_state.filters["uf"])
        busca_loja = c4.text_input("Buscar loja/cidade", value=st.session_state.filters["q"], placeholder="Digite parte do nome...")

        st.session_state.filters.update({"threshold": int(threshold), "uf": uf_filter, "q": busca_loja})

        destaques = []
        for loja, data in contagem_por_loja.items():
            if data["qtd"] >= threshold:
                row = {
                    "Loja": loja,
                    "Cidade": data["cidade"],
                    "UF": data["uf"],
                    "Chamados": data["qtd"],
                    "Últ. atualização": data["last_updated"].astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M") if data["last_updated"] else "—",
                    "⚠️": "🔴" if is_loja_critica(data) else "",
                }
                destaques.append(row)

        destaques = [
            r for r in destaques
            if (not uf_filter or (r["UF"] or "").upper() == uf_filter.strip().upper())
            and (not busca_loja or busca_loja.lower() in (r["Loja"] or "").lower()
                 or busca_loja.lower() in (r["Cidade"] or "").lower())
        ]

        if order_opt == "Chamados ↓":
            destaques.sort(key=lambda x: (-x["Chamados"], x["Loja"]))
        elif order_opt == "Loja ↑":
            destaques.sort(key=lambda x: (x["Loja"], -x["Chamados"]))
        else:
            destaques.sort(key=lambda x: ((x["Cidade"] or ""), x["Loja"]))

        st.caption(f"{len(destaques)} loja(s) encontradas após filtros.")
        st.dataframe(destaques, use_container_width=True, hide_index=True)

        if destaques:
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=["Loja", "Cidade", "UF", "Chamados", "Últ. atualização", "⚠️"])
            writer.writeheader()
            writer.writerows(destaques)
            st.download_button(
                "⬇️ Baixar CSV",
                data=output.getvalue().encode("utf-8"),
                file_name=f"lojas_destaque_{threshold}+_{datetime.now():%Y%m%d_%H%M%S}.csv",
                mime="text/csv"
            )
        else:
            st.info("Nenhuma loja atende aos filtros no momento.")

    st.markdown("")

    # Sub-abas: Pendentes | Agendados | TEC-CAMPO
    t1, t2, t3 = st.tabs(["⏳ Pendentes de Agendamento", "📋 Agendados", "🧰 TEC-CAMPO"])

    with t1:
        filtro_loja_pend = st.text_input("🔎 Filtrar por loja (código ou cidade) — Pendentes", "")
        if not pendentes_raw:
            st.warning("Nenhum chamado em **AGENDAMENTO**.")
        else:
            for loja, iss in sorted(jira.agrupar_chamados(pendentes_raw).items()):
                data = contagem_por_loja.get(loja, {"qtd": len(iss), "last_updated": None})
                alerta = " 🔴" if is_loja_critica(data) else ""
                if filtro_loja_pend:
                    if filtro_loja_pend.lower() not in loja.lower():
                        cidades = {x.get("cidade", "") for x in iss}
                        if not any(filtro_loja_pend.lower() in (c or "").lower() for c in cidades):
                            continue
                with st.expander(f"{alerta} {loja} — {len(iss)} chamado(s)", expanded=False):
                    st.code(gerar_mensagem(loja, iss), language="text")

    with t2:
        filtro_loja_ag = st.text_input("🔎 Filtrar por loja (código ou cidade) — Agendados", "")
        if not agendados_raw:
            st.info("Nenhum chamado em **Agendado**.")
        else:
            for date, stores in sorted(grouped_sched.items()):
                total = sum(len(v) for v in stores.values())
                st.subheader(f"{date} — {total} chamado(s)")
                for loja, iss in sorted(stores.items()):
                    data = contagem_por_loja.get(loja, {"qtd": len(iss), "last_updated": None})
                    alerta = " 🔴" if is_loja_critica(data) else ""

                    if filtro_loja_ag and filtro_loja_ag.lower() not in loja.lower():
                        cidades = {(x.get("fields", {}) or {}).get("customfield_11994") for x in iss}
                        if not any(filtro_loja_ag.lower() in (c or "").lower() for c in cidades):
                            continue

                    detalhes = jira.agrupar_chamados(iss)[loja]
                    dup_keys = [d["key"] for d in detalhes
                                if (d["pdv"], d["ativo"]) in verificar_duplicidade(detalhes)]

                    # Mantido: checagem spare por loja
                    spare_raw, _ = jira.buscar_chamados_enhanced(
                        f'project = FSA AND status = "Aguardando Spare" AND "Codigo da Loja[Dropdown]" = "{loja}"',
                        FIELDS, page_size=100
                    )
                    spare_keys = [i["key"] for i in (spare_raw or [])]

                    tags = []
                    if spare_keys: tags.append("Spare: " + ", ".join(spare_keys))
                    if dup_keys:   tags.append("Dup: " + ", ".join(dup_keys))
                    tag_str = f" [{' • '.join(tags)}]" if tags else ""

                    with st.expander(f"{alerta} {loja} — {len(iss)} chamado(s){tag_str}", expanded=False):
                        st.markdown("**FSAs:** " + ", ".join(d["key"] for d in detalhes))
                        st.code(gerar_mensagem(loja, detalhes), language="text")

    with t3:
        filtro_loja_tc = st.text_input("🔎 Filtrar por loja (código ou cidade) — TEC-CAMPO", "")
        if not tec_raw:
            st.info("Nenhum chamado em **TEC-CAMPO**.")
        else:
            for loja, iss in sorted(agrup_tec.items()):
                data = contagem_por_loja.get(loja, {"qtd": len(iss), "last_updated": None})
                alerta = " 🔴" if is_loja_critica(data) else ""
                if filtro_loja_tc:
                    if filtro_loja_tc.lower() not in loja.lower():
                        cidades = {x.get("cidade", "") for x in iss}
                        if not any(filtro_loja_tc.lower() in (c or "").lower() for c in cidades):
                            continue
                with st.expander(f"{alerta} {loja} — {len(iss)} chamado(s)", expanded=False):
                    st.code(gerar_mensagem(loja, iss), language="text")

    st.markdown("---")
    st.caption(f"Última atualização: {datetime.now():%d/%m/%Y %H:%M:%S}")

# ============================
# 📊 Visão Geral
# ============================
with tab_overview:
    # Presets
    with st.expander("🔖 Favoritos / Filtros salvos"):
        c1, c2 = st.columns([2, 1])
        with c1:
            st.write("Ajustes rápidos do painel:")
            st.session_state.filters["threshold"] = st.number_input(
                "Mín. chamados p/ destaque", min_value=2, max_value=50, value=int(st.session_state.filters["threshold"]), step=1
            )
            st.session_state.filters["uf"] = st.text_input("Filtrar UF (ex.: SP)", value=st.session_state.filters["uf"])
            st.session_state.filters["q"] = st.text_input("Buscar loja/cidade (destaques)", value=st.session_state.filters["q"])
            st.session_state.filters["days"] = st.slider(
                "Janela do gráfico (dias)", min_value=7, max_value=90, value=int(st.session_state.filters["days"]), step=1
            )
        with c2:
            preset_names = ["—"] + sorted(st.session_state.presets.keys())
            pick = st.selectbox("Carregar preset", preset_names, index=0)
            if pick != "—":
                if st.button("Carregar"):
                    st.session_state.filters.update(st.session_state.presets[pick])
                    st.success(f"Preset '{pick}' carregado.")
                    st.experimental_rerun()
                if st.button("Excluir"):
                    st.session_state.presets.pop(pick, None)
                    st.success(f"Preset '{pick}' excluído.")
            new_name = st.text_input("Salvar como…", "")
            if st.button("Salvar preset") and new_name.strip():
                st.session_state.presets[new_name.strip()] = dict(st.session_state.filters)
                st.success(f"Preset '{new_name.strip()}' salvo.")

    st.markdown("")
    colk1, colk2, colk3, colk4 = st.columns(4)
    colk1.metric("⏳ AGENDAMENTO", kpi["AGENDAMENTO"])
    colk2.metric("📋 Agendado",   kpi["Agendado"])
    colk3.metric("🧰 TEC-CAMPO",  kpi["TEC-CAMPO"])
    colk4.metric("🏷️ Lojas com 2+", sum(1 for x in contagem_por_loja.values() if x["qtd"] >= 2))

    st.markdown("")
    st.subheader("📌 Top 5 lojas mais críticas")
    if top_list:
        tcols = st.columns(len(top_list))
        for idx, card in enumerate(top_list):
            with tcols[idx]:
                indicador = "🔴 " if card["critica"] else ""
                last_upd = card["last_updated"].astimezone(timezone.utc).strftime("%d/%m %H:%M") if card["last_updated"] else "—"
                st.metric(
                    label=f"{indicador}{card['loja']} • {card['cidade']}-{card['uf']}",
                    value=card["qtd"],
                    delta=f"Últ. atualização: {last_upd}"
                )
    else:
        st.info("Sem dados para o ranking.")

    st.markdown("")
    st.subheader("📈 Tendência (últimos dias)")

    all_days = pd.date_range(
        (datetime.now() - timedelta(days=int(st.session_state.filters["days"]))).date(),
        datetime.now().date(),
        freq="D"
    )

    novos = [created_from_issue(i) for i in (combo_raw or [])]
    novos = [d for d in novos if d and (datetime.now(timezone.utc) - d) <= timedelta(days=int(st.session_state.filters["days"]))]
    df_novos = pd.Series(1, index=[d.date() for d in novos]).groupby(level=0).sum() if novos else pd.Series(dtype=int)

    resd = [resolutiondate_from_issue(i) for i in (resolvidos_raw or [])]
    resd = [d for d in resd if d and (datetime.now(timezone.utc) - d) <= timedelta(days=int(st.session_state.filters["days"]))]
    df_res = pd.Series(1, index=[d.date() for d in resd]).groupby(level=0).sum() if resd else pd.Series(dtype=int)

    chart_df = pd.DataFrame({
        "Novos": df_novos.reindex(all_days.date, fill_value=0),
        "Resolvidos": df_res.reindex(all_days.date, fill_value=0),
    }, index=[d.strftime("%d/%m") for d in all_days.date])

    st.line_chart(chart_df, use_container_width=True)

    st.markdown("")
    st.subheader("🗺️ Heatmap de lojas (auto, via endereço/CEP do Jira) — gratuito (OSM)")

    @st.cache_data(ttl=60*60*24, show_spinner=False)
    def geocode_nominatim(q: str):
        url = "https://nominatim.openstreetmap.org/search"
        headers = {"User-Agent": "FieldServiceDashboard/1.0 (contact: ops@empresa.com)"}
        params = {"q": q, "format": "json", "limit": 1, "countrycodes": "br"}
        try:
            r = requests.get(url, headers=headers, params=params, timeout=10)
            if r.status_code == 200 and r.json():
                item = r.json()[0]
                return float(item["lat"]), float(item["lon"])
        except Exception:
            return None
        return None

    pontos = []
    lojas_unicas = []
    for loja, data in contagem_por_loja.items():
        end = (data.get("endereco") or "").strip()
        cid = (data.get("cidade") or "").strip()
        uf  = (data.get("uf") or "").strip()
        cep = (data.get("cep") or "").strip()
        if not any([end, cid, uf, cep]):
            continue
        q = ", ".join([x for x in [end, cid, uf] if x]) + (f", {cep}" if cep else "") + ", Brasil"
        lojas_unicas.append((loja, q, data["qtd"]))

    with st.expander("⚙️ Configurar geocodificação", expanded=False):
        st.caption("Usa Nominatim (OSM) com cache de 24h.")
        max_geocode = st.slider("Máximo de lojas para geocodificar por execução", 10, 500, min(100, len(lojas_unicas)))
        pause = st.slider("Pausa entre chamadas (segundos)", 0.0, 2.0, 0.5, 0.1)
        run_geo = st.checkbox("Executar geocodificação agora", value=True)

    if run_geo and lojas_unicas:
        geocoded = 0
        for loja, query, peso in lojas_unicas[:max_geocode]:
            coords = geocode_nominatim(query)
            if coords:
                lat, lon = coords
                pontos += [{"lat": lat, "lon": lon} for _ in range(max(1, int(peso)))]
            geocoded += 1
            if pause > 0:
                time.sleep(pause)

        if pontos:
            st.map(pd.DataFrame(pontos), use_container_width=True)
        else:
            st.info("Nenhuma loja geocodificada com sucesso nesta execução.")
        st.caption(f"Geocodificadas: {geocoded} / {len(lojas_unicas)} loja(s)")
    else:
        st.info("Ative “Executar geocodificação agora” para gerar o mapa.")

    st.markdown("---")
    st.caption(f"Última atualização: {datetime.now():%d/%m/%Y %H:%M:%S}")
