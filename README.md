# notion_api

A shared Python module providing a Notion REST API client and data extraction utilities. Used by Notion_Automator, Notion_PowerBI, and Notion_Resume.

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

## Property builder helpers

Convenience static methods for building Notion property update payloads:

```python
NotionClient.date_property("2026-04-08")   # {"date": {"start": "2026-04-08"}}
NotionClient.number_property(42)           # {"number": 42}
NotionClient.rich_text_property("hello")   # {"rich_text": [...]}
NotionClient.checkbox_property(True)       # {"checkbox": True}
```
