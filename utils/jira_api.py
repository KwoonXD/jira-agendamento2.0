# utils/jira_api.py
import base64
import json
import requests
from requests.auth import HTTPBasicAuth
from collections import defaultdict
from typing import Tuple, Dict, Any, Optional, List


class JiraAPI:
    """
    Suporta dois modos:
      • Domínio: https://<site>.atlassian.net/rest/api/3/...
      • EX API : https://api.atlassian.com/ex/jira/{cloudId}/rest/api/3/...

    Para tokens fine-grained/OAuth, use EX API (use_ex_api=True) + cloud_id.
    Endpoints usados:
      - POST /rest/api/3/jql/parse
      - POST /rest/api/3/search/approximate-count
      - POST /rest/api/3/search/jql (enhanced search, com paginação via nextPageToken)
    """

    def __init__(
        self,
        email: str,
        api_token: str,
        jira_url: str,
        use_ex_api: bool = False,
        cloud_id: Optional[str] = None
    ):
        self.email = email.strip()
        self.api_token = api_token.strip()
        self.jira_url = jira_url.rstrip("/")
        self.use_ex_api = use_ex_api
        self.cloud_id = cloud_id

        self.auth = HTTPBasicAuth(self.email, self.api_token)
        self.hdr_json = {"Accept": "application/json", "Content-Type": "application/json"}
        self.hdr_accept = {"Accept": "application/json"}

        # debug da última chamada
        self.last_status = None
        self.last_error = None
        self.last_url = None
        self.last_params = None
        self.last_count = None
        self.last_method = None

    # ---------- base & headers ----------
    def _base(self) -> str:
        if self.use_ex_api:
            if not self.cloud_id:
                raise ValueError("cloud_id é obrigatório quando use_ex_api=True")
            return f"https://api.atlassian.com/ex/jira/{self.cloud_id}/rest/api/3"
        return f"{self.jira_url}/rest/api/3"

    def _auth_headers(self, json_content: bool = False) -> Dict[str, str]:
        """Na EX API a autenticação é via header Basic manual."""
        if not self.use_ex_api:
            return self.hdr_json if json_content else self.hdr_accept
        basic = f"{self.email}:{self.api_token}".encode("utf-8")
        base = {
            "Authorization": "Basic " + base64.b64encode(basic).decode("ascii"),
            "Accept": "application/json",
        }
        if json_content:
            base["Content-Type"] = "application/json"
        return base

    def _set_debug(self, url: str, params: Any, status: int, error: Any, count: int, method: str):
        self.last_url = url
        self.last_params = params
        self.last_status = status
        self.last_error = error
        self.last_count = count
        self.last_method = method

    def _req(self, method: str, url: str, *, json_body: Any = None, params: Dict[str, Any] = None, json_content=True):
        if self.use_ex_api:
            return requests.request(method, url, headers=self._auth_headers(json_content=json_content),
                                    data=(json.dumps(json_body) if json_body is not None else None),
                                    params=params)
        else:
            return requests.request(method, url, headers=(self.hdr_json if json_content else self.hdr_accept),
                                    auth=self.auth,
                                    json=(json_body if json_body is not None else None),
                                    params=params)

    # ---------- diagnóstico ----------
    def whoami(self) -> Tuple[Dict[str, Any] | None, Dict[str, Any]]:
        url = f"{self._base()}/myself"
        try:
            r = self._req("GET", url, json_content=False)
            dbg = {"url": url, "status": r.status_code}
            if r.status_code == 200:
                return r.json(), dbg
            dbg["error"] = _safe_json(r)
            return None, dbg
        except requests.RequestException as e:
            return None, {"url": url, "status": -1, "error": str(e)}

    def parse_jql(self, jql: str) -> Dict[str, Any]:
        url = f"{self._base()}/jql/parse"
        body = {"queries": [jql], "validation": "STRICT"}
        try:
            r = self._req("POST", url, json_body=body)
            out = {"url": url, "status": r.status_code}
            if r.status_code == 200:
                out["result"] = r.json()
            else:
                out["error"] = _safe_json(r)
            return out
        except requests.RequestException as e:
            return {"url": url, "status": -1, "error": str(e)}

    def count_jql(self, jql: str) -> Dict[str, Any]:
        url = f"{self._base()}/search/approximate-count"
        body = {"jql": jql}
        try:
            r = self._req("POST", url, json_body=body)
            out = {"url": url, "status": r.status_code}
            if r.status_code == 200:
                out["count"] = r.json().get("count", 0)
            else:
                out["error"] = _safe_json(r)
            return out
        except requests.RequestException as e:
            return {"url": url, "status": -1, "error": str(e)}

    # ---------- busca principal (ENHANCED) ----------
    def buscar_chamados_enhanced(self, jql: str, fields: str | List[str], page_size: int = 100, reconcile: bool = False) -> Tuple[List[dict], Dict[str, Any]]:
        """
        POST /search/jql com body JSON (jql, fields, maxResults) + paginação via nextPageToken.
        Retorna (issues, debug_dict)
        """
        base = self._base()
        url = f"{base}/search/jql"

        if isinstance(fields, str):
            fields_list = [f.strip() for f in fields.split(",") if f.strip()]
        else:
            fields_list = list(fields or [])

        issues: List[dict] = []
        next_page_token: Optional[str] = None
        last_resp = {}

        while True:
            body = {
                "jql": jql,
                "maxResults": int(page_size),
                "fields": fields_list
            }
            if reconcile:
                body["reconcileIssues"] = []
            if next_page_token:
                body["nextPageToken"] = next_page_token

            try:
                r = self._req("POST", url, json_body=body)
                if r.status_code != 200:
                    err = _safe_json(r)
                    self._set_debug(url, {"method": "POST", **body}, r.status_code, err, 0, "POST")
                    return [], {"url": url, "params": body, "status": r.status_code, "error": err, "count": 0, "method": "POST"}

                data = r.json()
                batch = data.get("issues", [])
                issues.extend(batch)
                next_page_token = data.get("nextPageToken")
                last_resp = {"url": url, "params": body, "status": 200, "count": len(batch), "method": "POST"}

                if not next_page_token:
                    break
            except requests.RequestException as e:
                self._set_debug(url, {"method": "POST", **body}, -1, str(e), 0, "POST")
                return [], {"url": url, "params": body, "status": -1, "error": str(e), "count": 0, "method": "POST"}

        self._set_debug(url, last_resp.get("params"), last_resp.get("status", 200), None, len(issues), "POST")
        return issues, {"url": url, "status": 200, "count": len(issues), "method": "POST"}

    # ---------- transições / leitura ----------
    def agrupar_chamados(self, issues: list) -> dict:
        agrup = defaultdict(list)
        for issue in issues:
            f = issue.get("fields", {})
            loja = f.get("customfield_14954", {}).get("value", "Loja Desconhecida")
            agrup[loja].append({
                "key": issue.get("key"),
                "pdv": f.get("customfield_14829", "--"),
                "ativo": f.get("customfield_14825", {}).get("value", "--"),
                "problema": f.get("customfield_12374", "--"),
                "endereco": f.get("customfield_12271", "--"),
                "estado": (f.get("customfield_11948") or {}).get("value", "--"),
                "cep": f.get("customfield_11993", "--"),
                "cidade": f.get("customfield_11994", "--"),
                "data_agendada": f.get("customfield_12036"),
            })
        return agrup

    def get_transitions(self, issue_key: str) -> list:
        url = f"{self._base()}/issue/{issue_key}/transitions"
        try:
            r = self._req("GET", url, json_content=False)
            if r.status_code == 200:
                return r.json().get("transitions", [])
        except requests.RequestException:
            pass
        return []

    def get_issue(self, issue_key: str) -> dict:
        url = f"{self._base()}/issue/{issue_key}"
        params = {"fields": "status"}
        try:
            r = self._req("GET", url, params=params, json_content=False)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass
        return {}

    def transicionar_status(self, issue_key: str, transition_id: str, fields: dict = None) -> requests.Response:
        url = f"{self._base()}/issue/{issue_key}/transitions"
        payload = {"transition": {"id": str(transition_id)}}
        if fields:
            payload["fields"] = fields
        return self._req("POST", url, json_body=payload)


def _safe_json(r: requests.Response):
    try:
        return r.json()
    except Exception:
        return r.text
