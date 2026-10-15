# notion_api

A shared Python module providing a Notion REST API client and data extraction utilities. Used by Notion_Automator and Notion_Analytics.

## What's in it

| Export               | Description                                           |
| ----------------------| -------------------------------------------------------|
| `NotionClient`       | Authenticated HTTP wrapper with automatic pagination  |
| `normalize_property` | Converts any Notion property value to a Python scalar |
| `extract_content`    | Fetches a page's block content and returns plain text |
| `extract_comments`   | Fetches a page's comments as a list of flat dicts     |

## Installation

**Option A — Install directly from GitHub (simplest):**

```bash
pip install git+https://github.com/DreamShark-Bytes/Notion_API.git
```

**Option B — Clone and install as editable (recommended if you'll be modifying the library):**

```bash
git clone https://github.com/DreamShark-Bytes/Notion_API.git
pip install -e ./Notion_API
```

The `-e` flag means **editable** — any changes made to `notion_api.py` take effect immediately in all projects without reinstalling.

## Usage

```python
from notion_api import NotionClient, normalize_property, extract_content, extract_comments

# Connect
client = NotionClient("secret_...")          # or omit token to use NOTION_TOKEN env var
client = NotionClient(debug=True)            # logs all API requests/responses
client = NotionClient(notion_version="2026-03-11")   # opt in to a newer API version (see "API versions")

# Databases
schema = client.get_database("db_id")
pages  = client.query_database("db_id")
pages  = client.query_database("db_id", filter_payload={...})  # with Notion filter

# Pages
page = client.get_page("page_id")
client.update_page_properties("page_id", {
    "Status": {"status": {"name": "Done"}},
    "Due Date": NotionClient.date_property("2026-04-08"),
})
client.create_page("db_id", {             # create a new page in a database
    "Name": {"title": [{"type": "text", "text": {"content": "New Task"}}]},
    "Status": {"status": {"name": "Not started"}},
})

# Blocks and comments
text     = extract_content(client, "page_id")    # → plain text string
comments = extract_comments(client, "page_id")   # → list of dicts

# Property normalization
value = normalize_property("select", prop_value)                          # → "Option Name" or None
value = normalize_property("date",   prop_value)                          # → "2026-01-01" or None
value = normalize_property("files",  prop_value)                          # → True / False (default)
value = normalize_property("files",  prop_value, files_handling="raw")    # → list of file objects
value = normalize_property("files",  prop_value, files_handling="skip")   # → None (ignore field)
```

## API versions (data sources)

`NotionClient` defaults to `Notion-Version: 2022-06-28`, so existing code behaves exactly as before. Pass `notion_version="2025-09-03"` (or later, e.g. `"2026-03-11"`) to use data sources:

```python
client = NotionClient(notion_version="2026-03-11")
pages  = client.query_database(any_id)        # database ID or data source ID; resolved and cached
schema = client.get_schema(any_id)            # ["properties"] on every version
ds_id  = client.resolve_data_source_id(db_id) # explicit lookup; raises ValueError if the database has several sources
client.query_data_source(ds_id, filter_payload={...}, sorts=[...])   # data-source versions only
client.create_page(any_id, {...})             # parent becomes {"type": "data_source_id", ...}
client.archive_page(page_id)                  # sends in_trash on 2026-03-11+, archived before
```

Notes:
- Notion IDs do not reveal their type, so `resolve_data_source_id` first tries the ID as a data source, then as a database (one extra call per ID, then cached).
- A database with more than one data source needs the data source ID; the old database endpoints stop working for it on every API version.
- `get_data_source`, `query_data_source` and `resolve_data_source_id` raise `ValueError` on legacy versions.
- `create_database` / `patch_database` send the new payload shapes (`initial_data_source`, schema via `/data_sources`) on data-source versions; verified live (2026-10-15) against a sandbox page with `tests/live_test.py` (opt-in: set `NOTION_TOKEN` and `NOTION_LIVE_PARENT`).

## Property builder helpers

Convenience static methods for building Notion property update payloads:

```python
NotionClient.date_property("2026-04-08")   # {"date": {"start": "2026-04-08"}}
NotionClient.number_property(42)           # {"number": 42}
NotionClient.rich_text_property("hello")   # {"rich_text": [...]}
NotionClient.checkbox_property(True)       # {"checkbox": True}
```
