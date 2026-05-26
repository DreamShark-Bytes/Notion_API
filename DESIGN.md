# Notion_API — Design

Shared library providing a Notion REST API client and data extraction utilities. Consumed by Notion_Automator and Notion_Analytics. Never contains business logic — only API communication and data normalization.

---

## Decision Log

### `files_handling` parameter on `normalize_property`

Three modes: `"bool"` (default), `"raw"`, `"skip"`.

- **Why not just return the URL?** Notion file URLs are short-lived signed S3 links — they expire and are not suitable for storage.
- **`"bool"`** — safe default for analytics: records whether files are attached without storing a useless expiring URL.
- **`"raw"`** — escape hatch for callers that need the full API response (e.g. to extract filenames or handle uploads).
- **`"skip"`** — lets callers exclude file columns entirely without adding them to an exclusion list.

---

### Block type handling in `extract_content`

Philosophy: **no block is silently dropped**. Every block type produces either text or a bracketed placeholder so the caller knows something was there.

| Category | Handling |
|---|---|
| Text blocks (paragraph, headings, lists, etc.) | Extracted as plain text, light markup preserved (`> quote`, `\| callout`, `[code:lang]`) |
| Table rows | Pipe-separated cell text |
| Media (image, video, audio, pdf) | `[type: caption]` — caption included if present |
| File attachments | `[file: name]` |
| Links (bookmark, embed, link_preview) | `[type: url]` |
| Child pages / inline databases | `[child_page: Title]` / `[child_database: Title]` — not recursed into to avoid unbounded API calls |
| Unsupported | `[unsupported]` |

`_SKIP_BLOCK_TYPES` is intentionally kept as an empty set. It exists as a named concept so future contributors have a clear place to add types that should be truly silent (if such a case ever arises), rather than scattering `continue` statements.

Child pages and databases are not recursed into because they are separate Notion objects with their own page IDs. Callers that want their content can fetch them explicitly.

---

### Version strategy

- Version is declared as `__version__` in `notion_api.py`.
- Consuming projects pin to a specific git tag in `requirements.txt` (e.g. `@v1.1.1`).
- Bump patch for backward-compatible additions or fixes. Bump minor for new exports or behavior changes. Bump major for breaking changes to existing exports.
- Tag on GitHub before consumers run `pip install -r requirements.txt`.

---

### `timeout=30` on all requests

A flat 30-second timeout covers both connect and read. Known limitation: for POST/PATCH requests, if Notion processes the write but the response is lost, the caller gets a timeout exception and may retry — resulting in a duplicate write. Notion's API is not idempotent for creates.

A `(connect_timeout, read_timeout)` tuple (e.g. `(10, 30)`) would be more precise but does not eliminate the silent-success risk. Deferred — see STATUS.md.

---

### Properties excluded from `normalize_property`

`created_by` and `last_edited_by` are excluded at the caller level (in `extractor.py` via `_SKIP_TYPES`), not here. The library normalizes any type it receives — exclusion is the caller's responsibility.
