"""
notion_api.py
Shared Notion API client and data extraction utilities.

Exports:
  NotionClient       — HTTP wrapper (auth, pagination, property builders)
  normalize_property — Notion property value → Python scalar
  extract_content    — page blocks → plain text string
  extract_comments   — page comments → list of dicts
"""

__version__ = "1.4.0"

import os
import logging
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

NOTION_VERSION = "2022-06-28"          # default; unchanged so existing consumers behave exactly as before
DATA_SOURCE_VERSION = "2025-09-03"     # first version with data sources (database query endpoint removed)
TRASH_VERSION = "2026-03-11"           # first version where `archived` is replaced by `in_trash`
BASE_URL = "https://api.notion.com/v1"


# ================================================================== #
#  HTTP Client
# ================================================================== #

class NotionClient:
    """Notion REST client.

    notion_version selects the API behavior. The default (2022-06-28) keeps the database-ID endpoints.
    With 2025-09-03 or later, database-style methods (query_database, create_page, get_schema, ...) accept
    either a database ID or a data source ID and resolve it to a data source for you (cached per client).
    """

    def __init__(self, token: Optional[str] = None, debug: bool = False, notion_version: str = NOTION_VERSION):
        self.token = token or os.environ["NOTION_TOKEN"]
        self.debug = debug
        self.notion_version = notion_version
        self._data_source_ids: dict[str, str] = {}
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Notion-Version": notion_version,
        }
        self._session = requests.Session()
        self._session.headers.update(self.headers)
        # Connection: close prevents stale keep-alive connections from being reused,
        # which causes SSL EOF errors when the server has already closed the socket.
        self._session.headers["Connection"] = "close"
        _retry = Retry(total=3, connect=3, backoff_factor=0.5)
        self._session.mount("https://", HTTPAdapter(max_retries=_retry))

    # ------------------------------------------------------------------ #
    #  Internal HTTP methods
    # ------------------------------------------------------------------ #

    def _log(self, method: str, path: str, payload: Optional[dict] = None, response: Optional[dict] = None):
        if not self.debug:
            return
        import json
        if payload is not None:
            logger.debug(f"[API] {method} {BASE_URL}{path} payload={json.dumps(payload)}")
        elif response is not None:
            logger.debug(f"[API] {method} {path} → {json.dumps(response)}")
        else:
            logger.debug(f"[API] {method} {BASE_URL}{path}")

    def _get(self, path: str) -> dict:
        self._log("GET", path)
        r = self._session.get(f"{BASE_URL}{path}", timeout=30)
        r.raise_for_status()
        data = r.json()
        self._log("GET", path, response=data)
        return data

    def _post(self, path: str, payload: dict) -> dict:
        self._log("POST", path, payload=payload)
        r = self._session.post(f"{BASE_URL}{path}", json=payload, timeout=30)
        if not r.ok:
            logger.debug(f"[API] POST {path} {r.status_code} error body: {r.text}")
        r.raise_for_status()
        data = r.json()
        self._log("POST", path, response=data)
        return data

    def _patch(self, path: str, payload: dict) -> dict:
        self._log("PATCH", path, payload=payload)
        r = self._session.patch(f"{BASE_URL}{path}", json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        self._log("PATCH", path, response=data)
        return data

    # ------------------------------------------------------------------ #
    #  Version helpers
    # ------------------------------------------------------------------ #

    @property
    def uses_data_sources(self) -> bool:
        """True when notion_version is 2025-09-03 or later (ISO dates compare correctly as strings)."""
        return self.notion_version >= DATA_SOURCE_VERSION

    def _require_data_sources(self, what: str):
        if not self.uses_data_sources:
            raise ValueError(f"{what} requires notion_version >= {DATA_SOURCE_VERSION} (client is on {self.notion_version})")

    # ------------------------------------------------------------------ #
    #  Data sources (notion_version >= 2025-09-03)
    # ------------------------------------------------------------------ #

    def get_data_source(self, data_source_id: str) -> dict:
        """Return a data source (its schema lives in ["properties"])."""
        self._require_data_sources("get_data_source")
        return self._get(f"/data_sources/{data_source_id}")

    def resolve_data_source_id(self, any_id: str) -> str:
        """Return a data source ID given either a data source ID or a database ID.

        Notion IDs do not reveal their type, so this tries the ID as a data source first, then as a database.
        A database with several data sources is ambiguous: pass the data source ID instead.
        """
        self._require_data_sources("resolve_data_source_id")
        if any_id in self._data_source_ids:
            return self._data_source_ids[any_id]
        try:
            self._get(f"/data_sources/{any_id}")
            resolved = any_id
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code not in (400, 404):
                raise
            sources = self._get(f"/databases/{any_id}").get("data_sources", [])
            if len(sources) != 1:
                names = ", ".join(f"{src.get('name')} ({src.get('id')})" for src in sources) or "none"
                raise ValueError(f"Database {any_id} has {len(sources)} data sources ({names}); pass a data source ID")
            resolved = sources[0]["id"]
        self._data_source_ids[any_id] = resolved
        return resolved

    def query_data_source(self, data_source_id: str, filter_payload: Optional[dict] = None, sorts: Optional[list] = None) -> list[dict]:
        """Return all pages from a data source, handling pagination automatically."""
        self._require_data_sources("query_data_source")
        payload: dict = {"page_size": 100}
        if filter_payload:
            payload["filter"] = filter_payload
        if sorts:
            payload["sorts"] = sorts
        return self._paginate_query(f"/data_sources/{data_source_id}/query", payload)

    def _paginate_query(self, path: str, payload: dict) -> list[dict]:
        results = []
        while True:
            data = self._post(path, payload)
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            payload["start_cursor"] = data["next_cursor"]
        return results

    # ------------------------------------------------------------------ #
    #  Database
    # ------------------------------------------------------------------ #

    def get_database(self, database_id: str) -> dict:
        """Return the database object. On notion_version >= 2025-09-03 this lists its data_sources but
        carries no property schema; use get_schema() for that."""
        return self._get(f"/databases/{database_id}")

    def get_schema(self, any_id: str) -> dict:
        """Return an object with ["properties"] (property definitions, status groups, etc.) on any version.

        On data-source versions any_id may be a database ID or a data source ID.
        """
        if self.uses_data_sources:
            return self.get_data_source(self.resolve_data_source_id(any_id))
        return self.get_database(any_id)

    def query_database(self, database_id: str, filter_payload: Optional[dict] = None) -> list[dict]:
        """Return all pages from a database, handling pagination automatically.

        On notion_version >= 2025-09-03, database_id may be a database ID (single data source) or a data source ID.
        """
        if self.uses_data_sources:
            return self.query_data_source(self.resolve_data_source_id(database_id), filter_payload)
        payload: dict = {"page_size": 100}
        if filter_payload:
            payload["filter"] = filter_payload
        return self._paginate_query(f"/databases/{database_id}/query", payload)

    def patch_database(
        self,
        database_id: str,
        properties: dict | None = None,
        title: str | None = None,
        description: str | None = None,
        icon: dict | None = None,
        is_inline: bool | None = None,
    ) -> dict:
        """Update a database — schema, title, description, icon, or inline mode.

        On notion_version >= 2025-09-03 `properties` (schema) is sent to the data source and the rest to the
        database; database_id may be a database ID or a data source ID. (Verified live 2026-10-15 on 2025-09-03 and 2026-03-11.)
        """
        if self.uses_data_sources and properties is not None:
            self._patch(f"/data_sources/{self.resolve_data_source_id(database_id)}", {"properties": properties})
            properties = None
            if title is None and description is None and icon is None and is_inline is None:
                return self.get_schema(database_id)
        body: dict = {}
        if properties is not None:
            body["properties"] = properties
        if title is not None:
            body["title"] = [{"type": "text", "text": {"content": title}}]
        if description is not None:
            body["description"] = [{"type": "text", "text": {"content": description}}]
        if icon is not None:
            body["icon"] = icon
        if is_inline is not None:
            body["is_inline"] = is_inline
        return self._patch(f"/databases/{database_id}", body)

    def create_database(self, parent_page_id: str, title: str, properties: dict, description: str | None = None, is_inline: bool = False, icon: dict | None = None) -> dict:
        """Create a new database as a child of a Notion page.

        is_inline=True renders the database table directly on the parent page
        rather than requiring the user to open it as a separate page.
        """
        body: dict = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "is_inline": is_inline,
        }
        # 2025-09-03+: initial schema goes under initial_data_source (verified live 2026-10-15)
        if self.uses_data_sources:
            body["initial_data_source"] = {"properties": properties}
        else:
            body["properties"] = properties
        if description is not None:
            body["description"] = [{"type": "text", "text": {"content": description}}]
        if icon is not None:
            body["icon"] = icon
        return self._post("/databases", body)

    # ------------------------------------------------------------------ #
    #  Pages
    # ------------------------------------------------------------------ #

    def get_page(self, page_id: str) -> dict:
        """Return a single page object."""
        return self._get(f"/pages/{page_id}")

    def update_page_properties(self, page_id: str, properties: dict, icon: dict | None = None) -> dict:
        """Patch one or more properties on a page. Optionally update the page icon."""
        body: dict = {"properties": properties}
        if icon is not None:
            body["icon"] = icon
        return self._patch(f"/pages/{page_id}", body)

    def create_page(self, database_id: str, properties: dict, icon: dict | None = None, template: dict | None = None) -> dict:
        """Create a new page in a database.

        On notion_version >= 2025-09-03 database_id may be a database ID or a data source ID; the page is created
        under the data source.
        """
        if self.uses_data_sources:
            parent = {"type": "data_source_id", "data_source_id": self.resolve_data_source_id(database_id)}
        else:
            parent = {"database_id": database_id}
        body: dict = {
            "parent": parent,
            "properties": properties,
        }
        if icon is not None:
            body["icon"] = icon
        if template is not None:
            body["template"] = template
        return self._post("/pages", body)

    def archive_page(self, page_id: str) -> dict:
        """Archive (delete) a page — removes it from database query results."""
        key = "in_trash" if self.notion_version >= TRASH_VERSION else "archived"
        return self._patch(f"/pages/{page_id}", {key: True})

    # ------------------------------------------------------------------ #
    #  Blocks (page content)
    # ------------------------------------------------------------------ #

    def append_blocks(self, block_id: str, children: list) -> dict:
        """Append child blocks to a page or block. Max 100 children per call."""
        return self._patch(f"/blocks/{block_id}/children", {"children": children})

    def get_blocks(self, block_id: str) -> list[dict]:
        """Return all child blocks for a page or block, handling pagination."""
        results = []
        cursor = None
        while True:
            path = f"/blocks/{block_id}/children?page_size=100"
            if cursor:
                path += f"&start_cursor={cursor}"
            data = self._get(path)
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data["next_cursor"]
        return results

    # ------------------------------------------------------------------ #
    #  Comments
    # ------------------------------------------------------------------ #

    def get_comments(self, page_id: str) -> list[dict]:
        """Return all comments for a page, handling pagination."""
        results = []
        cursor = None
        while True:
            path = f"/comments?block_id={page_id}&page_size=100"
            if cursor:
                path += f"&start_cursor={cursor}"
            data = self._get(path)
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data["next_cursor"]
        return results

    # ------------------------------------------------------------------ #
    #  Property value builders (for update_page_properties calls)
    # ------------------------------------------------------------------ #

    @staticmethod
    def date_property(iso_string: Optional[str]) -> dict:
        """Build a Notion date property value."""
        return {"date": {"start": iso_string} if iso_string else None}

    @staticmethod
    def number_property(value: float) -> dict:
        return {"number": value}

    @staticmethod
    def rich_text_property(text: str) -> dict:
        return {"rich_text": [{"type": "text", "text": {"content": text}}]}

    @staticmethod
    def checkbox_property(checked: bool) -> dict:
        return {"checkbox": checked}


# ================================================================== #
#  Property normalization
# ================================================================== #

# Block types that carry no readable text (skipped in extract_content)
_SKIP_BLOCK_TYPES: set = set()  # all block types are handled explicitly or via children


def _plain_text(rich_text_list: list) -> str:
    return "".join(rt.get("plain_text", "") for rt in rich_text_list)


def normalize_property(
    prop_type: str,
    prop_value: dict,
    files_handling: str = "bool",
) -> str | float | int | bool | list | None:
    """
    Return a scalar value for a Notion property.

    Args:
        prop_type:       Notion property type string (e.g. "select", "date", "files").
        prop_value:      Raw property value dict from the Notion API.
        files_handling:  Controls what is returned for "files"-type properties.
                         "bool" (default) — True if any files are attached, False otherwise.
                         "raw"            — the raw list of file objects from the API.
                         "skip"           — return None (ignore the field entirely).

    Returns comma-separated strings for multi-value types (multi_select, people, relation).
    """
    if prop_type == "title":
        return _plain_text(prop_value.get("title", []))

    if prop_type == "rich_text":
        return _plain_text(prop_value.get("rich_text", []))

    if prop_type == "number":
        return prop_value.get("number")

    if prop_type == "select":
        sel = prop_value.get("select")
        return sel["name"] if sel else None

    if prop_type == "multi_select":
        return ", ".join(o["name"] for o in prop_value.get("multi_select", []))

    if prop_type == "status":
        s = prop_value.get("status")
        return s["name"] if s else None

    if prop_type == "date":
        d = prop_value.get("date")
        if not d:
            return None
        start = d.get("start", "")
        end = d.get("end")
        return f"{start}/{end}" if end else start

    if prop_type == "checkbox":
        return prop_value.get("checkbox", False)

    if prop_type in ("url", "email", "phone_number"):
        return prop_value.get(prop_type)

    if prop_type == "people":
        people = prop_value.get("people", [])
        return ", ".join(p.get("name") or p.get("id", "") for p in people) or None

    if prop_type == "files":
        if files_handling == "bool":
            return bool(prop_value.get("files", []))
        if files_handling == "raw":
            return prop_value.get("files", [])
        return None  # "skip"

    if prop_type == "relation":
        ids = [r["id"] for r in prop_value.get("relation", [])]
        return ", ".join(ids) if ids else None

    if prop_type == "formula":
        f = prop_value.get("formula", {})
        ftype = f.get("type")
        if not ftype:
            return None
        val = f.get(ftype)
        if ftype == "date" and val:
            return val.get("start")
        return val

    if prop_type == "rollup":
        r = prop_value.get("rollup", {})
        rtype = r.get("type")
        if rtype == "number":
            return r.get("number")
        if rtype == "date":
            d = r.get("date")
            return d["start"] if d else None
        if rtype == "array":
            parts = []
            for item in r.get("array", []):
                itype = item.get("type")
                if itype:
                    v = normalize_property(itype, item)
                    if v is not None:
                        parts.append(str(v))
            return ", ".join(parts) or None
        return None

    if prop_type in ("created_time", "last_edited_time"):
        return prop_value.get(prop_type)

    if prop_type in ("created_by", "last_edited_by"):
        return prop_value.get(prop_type, {}).get("name")

    if prop_type == "unique_id":
        uid = prop_value.get("unique_id", {})
        prefix = uid.get("prefix") or ""
        number = uid.get("number")
        if number is None:
            return None
        return f"{prefix}-{number}" if prefix else str(number)

    if prop_type == "verification":
        v = prop_value.get("verification") or {}
        return v.get("state")

    logger.debug(f"Unknown property type '{prop_type}' — skipping")
    return None


# ================================================================== #
#  Page content → plain text
# ================================================================== #

def extract_content(client: NotionClient, page_id: str) -> str:
    """
    Fetch all blocks for a page and return them as plain text.
    Media blocks (image, video, audio, pdf, file) render as [type: caption/name].
    Link blocks (bookmark, embed, link_preview) render as [type: url].
    Tables render as pipe-separated rows.
    Child pages/databases are noted by title only (not recursed into).
    """
    try:
        blocks = client.get_blocks(page_id)
    except Exception as e:
        logger.warning(f"Could not fetch content for page {page_id}: {e}")
        return ""

    lines: list[str] = []
    _blocks_to_text(client, blocks, lines, depth=0)
    return "\n".join(lines).strip()


def _blocks_to_text(client: NotionClient, blocks: list, lines: list, depth: int):
    indent = "  " * depth

    for block in blocks:
        btype = block.get("type", "")

        if btype in _SKIP_BLOCK_TYPES:
            continue

        content = block.get(btype, {})
        text = _plain_text(content.get("rich_text", []))

        if btype == "paragraph":
            if text:
                lines.append(f"{indent}{text}")
        elif btype in ("heading_1", "heading_2", "heading_3"):
            if text:
                lines.append(f"{indent}{text}")
        elif btype == "bulleted_list_item":
            lines.append(f"{indent}• {text}")
        elif btype == "numbered_list_item":
            lines.append(f"{indent}{text}")
        elif btype == "to_do":
            mark = "[x]" if content.get("checked") else "[ ]"
            lines.append(f"{indent}{mark} {text}")
        elif btype == "toggle":
            if text:
                lines.append(f"{indent}{text}")
        elif btype == "quote":
            if text:
                lines.append(f"{indent}> {text}")
        elif btype == "callout":
            if text:
                lines.append(f"{indent}| {text}")
        elif btype == "code":
            lang = content.get("language", "")
            lines.append(f"{indent}[code:{lang}] {text}")
        elif btype == "divider":
            lines.append(f"{indent}---")
        elif btype == "table_row":
            cells = content.get("cells", [])
            row_text = " | ".join(_plain_text(cell) for cell in cells)
            if row_text.strip():
                lines.append(f"{indent}{row_text}")
        elif btype in ("image", "video", "audio", "pdf"):
            caption = _plain_text(content.get("caption", []))
            lines.append(f"{indent}[{btype}]" + (f": {caption}" if caption else ""))
        elif btype == "file":
            label = content.get("name", "") or _plain_text(content.get("caption", []))
            lines.append(f"{indent}[file]" + (f": {label}" if label else ""))
        elif btype in ("bookmark", "embed", "link_preview"):
            url = content.get("url", "")
            lines.append(f"{indent}[{btype}]" + (f": {url}" if url else ""))
        elif btype == "unsupported":
            lines.append(f"{indent}[unsupported]")
        elif btype in ("child_page", "child_database"):
            title = content.get("title", "")
            if title:
                lines.append(f"{indent}[{btype}: {title}]")
            continue  # don't recurse into child pages

        if block.get("has_children") and btype not in ("child_page", "child_database"):
            try:
                children = client.get_blocks(block["id"])
                _blocks_to_text(client, children, lines, depth + 1)
            except Exception as e:
                logger.warning(f"Could not fetch children of block {block['id']}: {e}")


# ================================================================== #
#  Comments
# ================================================================== #

def extract_comments(client: NotionClient, page_id: str) -> list[dict]:
    """
    Return a list of flat comment dicts for a page.
    Each dict: {comment_id, page_id, created_time, last_edited_time, text}
    """
    try:
        raw = client.get_comments(page_id)
    except Exception as e:
        logger.warning(f"Could not fetch comments for page {page_id}: {e}")
        return []

    return [
        {
            "comment_id": c["id"],
            "page_id": page_id,
            "created_time": c.get("created_time"),
            "last_edited_time": c.get("last_edited_time"),
            "text": _plain_text(c.get("rich_text", [])),
        }
        for c in raw
    ]
