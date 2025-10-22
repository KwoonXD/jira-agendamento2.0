"""Shared constants for the Field Service Streamlit dashboard."""

from typing import Final, Mapping

# ---- Custom field identifiers ----
CUSTOMFIELD_LOJA: Final[str] = "customfield_14954"
CUSTOMFIELD_PDV: Final[str] = "customfield_14829"
CUSTOMFIELD_ATIVO: Final[str] = "customfield_14825"
CUSTOMFIELD_PROBLEMA: Final[str] = "customfield_12374"
CUSTOMFIELD_ENDERECO: Final[str] = "customfield_12271"
CUSTOMFIELD_CEP: Final[str] = "customfield_11993"
CUSTOMFIELD_CIDADE: Final[str] = "customfield_11994"
CUSTOMFIELD_UF: Final[str] = "customfield_11948"
CUSTOMFIELD_DATA_AGENDA: Final[str] = "customfield_12036"
CUSTOMFIELD_TECNICOS: Final[str] = "customfield_12279"

# ---- Default fields to request from Jira ----
FIELDS: Final[str] = (
    "summary,"
    f"{CUSTOMFIELD_LOJA},{CUSTOMFIELD_PDV},{CUSTOMFIELD_ATIVO},"
    f"{CUSTOMFIELD_PROBLEMA},{CUSTOMFIELD_ENDERECO},{CUSTOMFIELD_CEP},"
    f"{CUSTOMFIELD_CIDADE},{CUSTOMFIELD_UF},{CUSTOMFIELD_DATA_AGENDA},"
    f"{CUSTOMFIELD_TECNICOS},"
    "status,created,resolutiondate,updated"
)

# ---- Status names and IDs ----
STATUS_NAME_AGENDAMENTO: Final[str] = "AGENDAMENTO"
STATUS_NAME_AGENDADO: Final[str] = "Agendado"
STATUS_NAME_TEC_CAMPO: Final[str] = "TEC-CAMPO"

STATUS_ID_AGENDAMENTO: Final[str] = "11499"
STATUS_ID_AGENDADO: Final[str] = "11481"
STATUS_ID_TEC_CAMPO: Final[str] = "11500"

STATUS_NAMES_MONITORED: Final[tuple[str, ...]] = (
    STATUS_NAME_AGENDAMENTO,
    STATUS_NAME_AGENDADO,
    STATUS_NAME_TEC_CAMPO,
)

STATUS_ID_TO_NAME: Final[Mapping[str, str]] = {
    STATUS_ID_AGENDAMENTO: STATUS_NAME_AGENDAMENTO,
    STATUS_ID_AGENDADO: STATUS_NAME_AGENDADO,
    STATUS_ID_TEC_CAMPO: STATUS_NAME_TEC_CAMPO,
}

# ---- JQL snippets ----
JQL_PEND: Final[str] = (
    f'project = FSA AND status = "{STATUS_NAME_AGENDAMENTO}" ORDER BY updated DESC'
)
JQL_AG: Final[str] = f'project = FSA AND status = "{STATUS_NAME_AGENDADO}" ORDER BY updated DESC'
JQL_TC: Final[str] = f'project = FSA AND status = "{STATUS_NAME_TEC_CAMPO}" ORDER BY updated DESC'
JQL_COMBINADA: Final[str] = (
    f"project = FSA AND status in ({STATUS_ID_AGENDAMENTO},{STATUS_ID_AGENDADO},{STATUS_ID_TEC_CAMPO})"
)
JQL_RESOLVIDOS_BASE: Final[str] = (
    'project = FSA AND status in (11498, 10702, "Encerrado", "Resolvido") '
    'AND resolutiondate >= "{from_iso}" AND resolutiondate <= "{to_iso}"'
)
