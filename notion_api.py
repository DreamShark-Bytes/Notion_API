"""
notion_api.py
Shared Notion API client and data extraction utilities.

Exports:
  NotionClient       — HTTP wrapper (auth, pagination, property builders)
  normalize_property — Notion property value → Python scalar
  extract_content    — page blocks → plain text string
  extract_comments   — page comments → list of dicts
"""

__version__ = "1.0.1"

import os
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

NOTION_VERSION = "2022-06-28"
BASE_URL = "https://api.notion.com/v1"


# ================================================================== #
#  HTTP Client
# ================================================================== #

class NotionClient:
    def __init__(self, token: Optional[str] = None, debug: bool = False):
        self.token = token or os.environ["NOTION_TOKEN"]
        self.debug = debug
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        }

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
        r = requests.get(f"{BASE_URL}{path}", headers=self.headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        self._log("GET", path, response=data)
        return data

    def _post(self, path: str, payload: dict) -> dict:
        self._log("POST", path, payload=payload)
        r = requests.post(f"{BASE_URL}{path}", json=payload, headers=self.headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        self._log("POST", path, response=data)
        return data

    def _patch(self, path: str, payload: dict) -> dict:
        self._log("PATCH", path, payload=payload)
        r = requests.patch(f"{BASE_URL}{path}", json=payload, headers=self.headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        self._log("PATCH", path, response=data)
        return data

    # ------------------------------------------------------------------ #
    #  Database
    # ------------------------------------------------------------------ #

    def get_database(self, database_id: str) -> dict:
        """Return the database schema (property definitions, status groups, etc.)."""
        return self._get(f"/databases/{database_id}")

    def query_database(self, database_id: str, filter_payload: Optional[dict] = None) -> list[dict]:
        """Return all pages from a database, handling pagination automatically."""
        path = f"/databases/{database_id}/query"
        payload: dict = {"page_size": 100}
        if filter_payload:
            payload["filter"] = filter_payload

        results = []
        while True:
            data = self._post(path, payload)
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            payload["start_cursor"] = data["next_cursor"]

        return results

    # ------------------------------------------------------------------ #
    #  Pages
    # ------------------------------------------------------------------ #

    def get_page(self, page_id: str) -> dict:
        """Return a single page object."""
        return self._get(f"/pages/{page_id}")

    def update_page_properties(self, page_id: str, properties: dict) -> dict:
        """Patch one or more properties on a page."""
        return self._patch(f"/pages/{page_id}", {"properties": properties})

    def create_page(self, database_id: str, properties: dict) -> dict:
        """Create a new page in a database."""
        return self._post("/pages", {
            "parent": {"database_id": database_id},
            "properties": properties,
        })

    # ------------------------------------------------------------------ #
    #  Blocks (page content)
    # ------------------------------------------------------------------ #

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
_SKIP_BLOCK_TYPES = {
    "image", "file", "pdf", "video", "audio",
    "embed", "bookmark", "link_preview", "unsupported",
}


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
    Images, files, embeds, and bookmarks are skipped.
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
