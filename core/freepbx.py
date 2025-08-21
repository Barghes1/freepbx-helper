import time
import re
import logging
from typing import Dict, List, Tuple, Optional

import requests

log = logging.getLogger(__name__)


class AlreadyExists(Exception):
    """Raised when an entity already exists on FreePBX side."""
    pass


class FreePBX:
    def __init__(self, base_url: str, client_id: str, client_secret: str, verify: bool = True):
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.verify = verify
        self.token: Optional[str] = None
        self.token_exp: float = 0.0

    # ----- URLs -----
    @property
    def token_url(self) -> str:
        return f"{self.base_url}/admin/api/api/token"

    @property
    def gql_url(self) -> str:
        return f"{self.base_url}/admin/api/api/gql"

    # ----- Auth / GQL -----
    def ensure_token(self) -> None:
        now = time.time()
        if self.token and now < self.token_exp - 30:
            return
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "gql gql:core",
        }
        r = requests.post(self.token_url, data=data, timeout=25, verify=self.verify)
        r.raise_for_status()
        j = r.json()
        self.token = j["access_token"]
        self.token_exp = now + int(j.get("expires_in", 3600))

    def gql(self, query: str, variables: Optional[dict] = None) -> dict:
        self.ensure_token()
        h = {"Authorization": f"Bearer {self.token}"}
        r = requests.post(
            self.gql_url,
            json={"query": query, "variables": variables or {}},
            timeout=35,
            verify=self.verify,
            headers=h,
        )
        r.raise_for_status()
        js = r.json()
        if "errors" in js:
            raise RuntimeError(js["errors"])
        return js["data"]

    # ----- Extensions: read -----
    def fetch_all_extensions(self) -> List[Tuple[str, str]]:
        q_full = """
        query {
          fetchAllExtensions {
            extension {
              extensionId
              tech
              pjsip { secret }
              user { password extPassword }
            }
          }
        }
        """
        q_fallback = """
        query {
          fetchAllExtensions {
            extension {
              extensionId
              user { password extPassword }
            }
          }
        }
        """
        try:
            data = self.gql(q_full)
            exts = data["fetchAllExtensions"]["extension"]
        except Exception:
            data = self.gql(q_fallback)
            exts = data["fetchAllExtensions"]["extension"]

        out: List[Tuple[str, str]] = []
        for e in exts:
            ext = str(e["extensionId"])
            u = e.get("user") or {}
            pw = u.get("extPassword") or (e.get("pjsip", {}) or {}).get("secret") or u.get("password") or ""
            out.append((ext, pw))
        out.sort(key=lambda x: int(re.sub(r"\D", "", x[0]) or 0))
        return out

    def fetch_ext_index(self):
        queries = [
            """
            query {
              fetchAllExtensions {
                extension {
                  extensionId
                  user { extPassword name displayname }
                }
              }
            }
            """,
            """
            query {
              fetchAllExtensions {
                extension {
                  extensionId
                  user { password }
                }
              }
            }
            """,
            """
            query {
              fetchAllExtensions {
                extension { extensionId }
              }
            }
            """,
        ]

        def pick_name(e: dict) -> str:
            u = e.get("user") or {}
            for k in ("name", "displayname", "username"):
                v = u.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            return ""

        for q in queries:
            try:
                data = self.gql(q)
                exts = data["fetchAllExtensions"]["extension"]
                by_ext: Dict[str, Dict[str, str]] = {}
                name_set = set()
                for e in exts:
                    ext = str(e["extensionId"])
                    name = pick_name(e)
                    by_ext[ext] = {"name": name, "pw": ""}  # pw не трогаем здесь
                    if name:
                        name_set.add(name.lower())
                return by_ext, name_set, bool(name_set)
            except Exception:
                continue

        return {}, set(), False

    # ----- Extensions: write -----
    def delete_extension(self, extension: str) -> None:
        ext_str = str(extension)
        variants = [
            ("ID",     "id",           "input"),
            ("String", "id",           "input"),
            ("ID",     "extensionId",  "input"),
            ("String", "extensionId",  "input"),
            ("ID",     "extension",    "input"),
            ("String", "extension",    "input"),
            ("ID",     "extId",        "input"),
            ("String", "extId",        "input"),
            ("ID",     "id",           "direct"),
            ("String", "id",           "direct"),
            ("ID",     "extensionId",  "direct"),
            ("String", "extensionId",  "direct"),
            ("ID",     "extension",    "direct"),
            ("String", "extension",    "direct"),
            ("ID",     "extId",        "direct"),
            ("String", "extId",        "direct"),
        ]
        last_err = None
        for typ, field, mode in variants:
            if mode == "input":
                m = f"""
                mutation($ext: {typ}!) {{
                  deleteExtension(input: {{ {field}: $ext }}) {{ status message }}
                }}
                """
            else:
                m = f"""
                mutation($ext: {typ}!) {{
                  deleteExtension({field}: $ext) {{ status message }}
                }}
                """
            try:
                self.gql(m, {"ext": ext_str})
                return
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"deleteExtension failed (all variants): {last_err}")

    def create_one(self, ext: int, name: Optional[str] = None) -> None:
        m = """
        mutation($start: ID!, $name: String!, $email: String!) {
          createRangeofExtension(input:{
            startExtension: $start,
            numberOfExtensions: 1,
            tech: "pjsip",
            name: $name,
            email: $email,
            vmEnable: true,
            umEnable: true
          }) {
            status
            message
          }
        }
        """
        nm = str(name).strip() if (name and str(name).strip()) else str(ext)
        vars = {
            "start": str(ext),
            "name": nm,
            "email": f"{ext}@local",
        }
        self.gql(m, vars)

    def set_ext_password(self, extension: str, secret: str) -> None:
        m_id = """
        mutation($extId: ID!, $name: String!, $pwd: String!) {
          updateExtension(input: {
            extensionId: $extId,
            name: $name,
            extPassword: $pwd
          }) { status message }
        }
        """
        m_str = """
        mutation($extId: String!, $name: String!, $pwd: String!) {
          updateExtension(input: {
            extensionId: $extId,
            name: $name,
            extPassword: $pwd
          }) { status message }
        }
        """
        vars_id = {"extId": str(extension), "name": str(extension), "pwd": secret}
        try:
            self.gql(m_id, vars_id)
        except Exception as e1:
            vars_str = {"extId": str(extension), "name": str(extension), "pwd": secret}
            try:
                self.gql(m_str, vars_str)
            except Exception as e2:
                raise RuntimeError(f"updateExtension failed: ID! -> {e1}; String! -> {e2}")

    # ----- Apply Config -----
    def apply_config(self) -> dict:
        gql_mutation = """
        mutation {
            doreload(input: {}) {
              status
              message
              transaction_id
            }
        }
        """
        try:
            data = self.gql(gql_mutation)
            return data.get("doreload") or {"status": True, "message": "doreload ok"}
        except Exception as e1:
            url = f"{self.base_url}/admin/ajax.php"
            try:
                r = requests.get(url, params={"command": "reload"}, timeout=25, verify=self.verify)
                r.raise_for_status()
                try:
                    return {"status": True, "message": str(r.json())[:400]}
                except ValueError:
                    return {"status": True, "message": r.text[:400]}
            except Exception as e2:
                raise RuntimeError(f"Apply Config failed: GraphQL doreload -> {e1}; ajax reload -> {e2}")

    # ----- Inbound Routes -----
    def create_inbound_route(self, did: str, description: str, ext: str) -> None:
        did = str(did).strip()
        description = str(description).strip()
        ext = str(ext).strip()

        candidates = [
            f"from-did-direct,{ext},1",
            f"ext-local,{ext},1",
        ]

        def _post_gql(mutation: str, variables: dict):
            self.ensure_token()
            h = {"Authorization": f"Bearer {self.token}"}
            resp = requests.post(
                self.gql_url,
                json={"query": mutation, "variables": variables},
                timeout=35,
                verify=self.verify,
                headers=h,
            )
            text = resp.text
            try:
                data = resp.json()
            except Exception:
                data = None

            if not resp.ok:
                lower = (text or "").lower()
                if any(k in lower for k in ("already", "exist", "duplicate", "unique")):
                    raise AlreadyExists(text[:300])
                resp.raise_for_status()

            if isinstance(data, dict) and "errors" in data:
                joined = " | ".join(str(e.get("message", "")) for e in data["errors"])
                lower = joined.lower()
                if any(k in lower for k in ("already", "exist", "duplicate", "unique")):
                    raise AlreadyExists(joined[:300])
                raise RuntimeError(joined or "GraphQL error")

            return data

        mutation = """
        mutation($did:String!, $desc:String!, $dest:String!) {
            addInboundRoute(input:{
                extension: $did,
                description: $desc,
                destination: $dest
            }) {
                status
                message
                inboundRoute { id }
            }
        }"""

        last_err: Optional[Exception] = None
        for dest in candidates:
            try:
                _post_gql(mutation, {"did": did, "desc": description, "dest": dest})
                return
            except AlreadyExists:
                raise
            except Exception as e:
                last_err = e
                continue

        msg = str(last_err) if last_err else ""
        if "Cannot query field" in msg and "addInboundRoute" in msg:
            raise RuntimeError(
                "На этой версии FreePBX отсутствует мутация addInboundRoute. "
                "Обнови модули framework/core/api до последних версий."
            )
        raise RuntimeError(f"create_inbound_route failed: {last_err}")
    
    # ===== Inbound Routes: find =====
    def _try_fetch_inbound_routes(self) -> list:
        queries = [
            """
            query {
              fetchAllInboundRoutes {
                inboundRoute { id extension description destination }
              }
            }
            """,
            """
            query {
              inboundRoutes {
                inboundRoute { id extension description destination }
              }
            }
            """,
            """
            query {
              fetchInboundRoutes {
                inboundRoute { id extension description destination }
              }
            }
            """,
        ]
        last_err = None
        for q in queries:
            try:
                data = self.gql(q)
                # пытаемся вытащить первый встретившийся контейнер
                for key in ("fetchAllInboundRoutes", "inboundRoutes", "fetchInboundRoutes"):
                    if key in data and data[key] and "inboundRoute" in data[key]:
                        arr = data[key]["inboundRoute"] or []
                        # normalize
                        out = []
                        for r in arr:
                            out.append({
                                "id": str(r.get("id")) if r.get("id") is not None else None,
                                "extension": str(r.get("extension") or "").strip(),
                                "description": str(r.get("description") or "").strip(),
                                "destination": str(r.get("destination") or "").strip(),
                            })
                        return out
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"Не удалось получить список inbound routes: {last_err}")

    def find_inbound_route(self, did: str) -> Optional[dict]:
        """
        Возвращает словарь маршрута по DID (extension) или None.
        """
        did = str(did).strip()
        routes = self._try_fetch_inbound_routes()
        for r in routes:
            if r.get("extension") == did:
                return r
        return None

    def delete_inbound_route(self, route_id: str):
        q = """
        mutation ($input: removeInboundRouteInput!) {
        removeInboundRoute(input: $input) {
            status
            message
        }
        }
        """
        variables = {"input": {"id": route_id}}
        data = self.gql(q, variables)
        return data.get("removeInboundRoute") or {}



    
    def list_inbound_routes(self):
        """
        Возвращает список inbound routes: id, extension, description
        """
        q = """
        query {
        allInboundRoutes {
            inboundRoutes {
            id
            extension
            description
            }
        }
        }
        """
        data = self.gql(q)
        conns = data.get("allInboundRoutes") or {}
        routes = conns.get("inboundRoutes") or []
        out = []
        for r in routes:
            out.append({
                "id": str(r.get("id") or ""),
                "extension": str(r.get("extension") or ""),
                "description": str(r.get("description") or ""),
            })
        return out
    
    def list_query_fields(self):
        q = """
        query {
        __schema {
            queryType {
            fields { name }
            }
        }
        }
        """
        data = self.gql(q)
        return [f["name"] for f in data["__schema"]["queryType"]["fields"]]
    
    def list_mutations(self):
        q = """
        query {
        __schema {
            mutationType {
            fields { name }
            }
        }
        }
        """
        data = self.gql(q)
        return [f["name"] for f in data["__schema"]["mutationType"]["fields"]]








