"""Minimal Supabase client: PostgREST for the tables and functions, Storage for uploaded audio."""

import uuid
from urllib.parse import quote

import httpx


class SupabaseError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Supabase returned HTTP {status}: {message}")
        self.status = status


class Supabase:
    """Uses the server-side key. New secret keys (sb_secret_...) go in the apikey header only:
    they are not JWTs, so they must not be sent as a Bearer token. Legacy service_role keys
    are JWTs and also need the Authorization header."""

    def __init__(self, url: str, key: str, client: httpx.Client | None = None):
        if not url or not key:
            raise SupabaseError(0, "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        self.rest = url.rstrip("/") + "/rest/v1"
        self.storage = url.rstrip("/") + "/storage/v1"
        self.headers = {"apikey": key, "Content-Type": "application/json"}
        if key.startswith("eyJ"):  # legacy JWT key
            self.headers["Authorization"] = f"Bearer {key}"
        self.client = client or httpx.Client(timeout=30)

    def _request(self, method: str, table: str, *, params=None, json=None, prefer: str | None = None, base: str | None = None,
                 content: bytes | None = None, extra_headers: dict | None = None):
        headers = {**self.headers, **(extra_headers or {})}
        if prefer:
            headers["Prefer"] = prefer
        response = self.client.request(method, f"{base or self.rest}/{table}", params=params, json=json, content=content,
                                       headers=headers)
        if response.status_code >= 400:
            try:
                body = response.json()
                message = f"{body.get('code', '')} {body.get('message', '')}".strip()
            except (ValueError, AttributeError):
                message = response.text[:200]
            raise SupabaseError(response.status_code, message)
        return response.json() if response.content else None

    def insert_call(self, **fields) -> dict:
        return self._request("POST", "calls", json=fields, prefer="return=representation")[0]

    def update_call(self, call_id: str, **fields) -> None:
        self._request("PATCH", "calls", params={"id": f"eq.{call_id}"}, json=fields, prefer="return=minimal")

    def save_transcript(self, call_id: str, utterances: list[dict], provider: str = "deepgram") -> None:
        self._request("POST", "transcripts", params={"on_conflict": "call_id"},
                      json={"call_id": call_id, "utterances": utterances, "provider": provider},
                      prefer="resolution=merge-duplicates,return=minimal")

    def get_call(self, call_id: str) -> dict | None:
        """The call with its transcript and all scores (newest first), each with the rubric version it used.
        Call ids are UUIDs: anything else cannot exist, so it is answered here instead of as a database error."""
        try:
            call_id = str(uuid.UUID(call_id))
        except (ValueError, AttributeError, TypeError):
            return None
        rows = self._request("GET", "calls", params={
            "id": f"eq.{call_id}", "select": "*,transcripts(utterances,provider),scores(*,rubrics(slug,name,version,items))",
            "scores.order": "created_at.desc"})
        return rows[0] if rows else None

    def list_calls(self, limit: int = 50, offset: int = 0, demo_only: bool = False) -> list[dict]:
        """Newest calls first, each with only its latest score."""
        params = {
            # "*" rather than a column list, so the page works before and after optional columns (title) exist.
            "select": "*,scores(overall_score,passed,items,cost_usd,latency_ms,created_at,rubrics(slug,name,version,items))",
            "order": "created_at.desc", "limit": limit, "offset": offset,
            "scores.order": "created_at.desc", "scores.limit": 1}
        if demo_only:
            params["is_demo"] = "eq.true"
        return self._request("GET", "calls", params=params)

    def get_rubric(self, slug: str, version: int) -> dict | None:
        rows = self._request("GET", "rubrics", params={"slug": f"eq.{slug}", "version": f"eq.{version}", "select": "id"})
        return rows[0] if rows else None

    def insert_rubric(self, **fields) -> dict:
        return self._request("POST", "rubrics", json=fields, prefer="return=representation")[0]

    def delete_rubric(self, rubric_id: str) -> None:
        self._request("DELETE", "rubrics", params={"id": f"eq.{rubric_id}"}, prefer="return=minimal")

    def insert_score(self, **fields) -> dict:
        return self._request("POST", "scores", json=fields, prefer="return=representation")[0]

    def rpc(self, function: str, **args):
        """Calls a Postgres function through PostgREST (POST /rpc/<function>)."""
        return self._request("POST", f"rpc/{function}", json=args)

    def signed_upload_url(self, bucket: str, path: str) -> str:
        """URL the browser can PUT one file to, without any key. Valid for two hours (Storage default)."""
        data = self._request("POST", f"object/upload/sign/{bucket}/{quote(path)}", json={}, base=self.storage)
        return self.storage + data["url"]

    def upload_object(self, bucket: str, path: str, data: bytes, content_type: str) -> None:
        """Puts a file in Storage, replacing one at the same path."""
        self._request("POST", f"object/{bucket}/{quote(path)}", base=self.storage, content=data,
                      extra_headers={"Content-Type": content_type, "x-upsert": "true"})

    def remove_objects(self, bucket: str, paths: list[str]) -> None:
        self._request("DELETE", f"object/{bucket}", base=self.storage, json={"prefixes": paths})

    def signed_download_url(self, bucket: str, path: str, seconds: int) -> str:
        """Short-lived URL for reading one private object, for example by Deepgram."""
        data = self._request("POST", f"object/sign/{bucket}/{quote(path)}", json={"expiresIn": seconds}, base=self.storage)
        return self.storage + data["signedURL"]

    def delete_call(self, call_id: str) -> None:
        """Deletes the call; its transcript and scores go with it (on delete cascade)."""
        self._request("DELETE", "calls", params={"id": f"eq.{call_id}"}, prefer="return=minimal")
