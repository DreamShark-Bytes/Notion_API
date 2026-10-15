"""Opt-in live test against a real Notion sandbox page. Skipped unless both env vars are set:

  NOTION_TOKEN=...  NOTION_LIVE_PARENT=<sandbox page id>  pytest tests/live_test.py -s

For each API version it creates a database under the sandbox page, exercises schema read/patch, page create/update/query/
archive by database ID (and by data source ID on 2025-09-03+), then trashes the database. Nothing outside the sandbox is touched.
"""
import os
import time

import pytest

from notion_api import NotionClient, TRASH_VERSION

TOKEN, PARENT = os.environ.get("NOTION_TOKEN"), os.environ.get("NOTION_LIVE_PARENT")
pytestmark = pytest.mark.skipif(not (TOKEN and PARENT), reason="set NOTION_TOKEN and NOTION_LIVE_PARENT to run live tests")

VERSIONS = ["2022-06-28", "2025-09-03", "2026-03-11"]


def title(text):
    return {"title": [{"text": {"content": text}}]}


@pytest.mark.parametrize("version", VERSIONS)
def test_roundtrip(version):
    c = NotionClient(TOKEN, notion_version=version)
    db = c.create_database(PARENT, f"live-test {version}", {"Name": {"title": {}}, "Note": {"rich_text": {}}})
    db_id = db["id"]
    try:
        ds_id = db["data_sources"][0]["id"] if c.uses_data_sources else None
        time.sleep(1)

        schema = c.get_schema(db_id)
        assert "Name" in schema["properties"] and "Note" in schema["properties"]

        c.patch_database(db_id, properties={"Score": {"number": {}}})
        assert "Score" in c.get_schema(db_id)["properties"]

        page = c.create_page(db_id, {"Name": title("first"), "Score": {"number": 1}})
        c.update_page_properties(page["id"], {"Score": {"number": 2}})
        assert c.get_page(page["id"])["properties"]["Score"]["number"] == 2

        assert [p["id"] for p in c.query_database(db_id)] == [page["id"]]
        if ds_id:
            assert [p["id"] for p in c.query_database(ds_id)] == [page["id"]]
            p2 = c.create_page(ds_id, {"Name": title("second")})
            assert len(c.query_database(db_id)) == 2
            c.archive_page(p2["id"])

        c.archive_page(page["id"])
        assert c.query_database(db_id) == []
    finally:
        key = "in_trash" if version >= TRASH_VERSION else "archived"
        c._patch(f"/databases/{db_id}", {key: True})
