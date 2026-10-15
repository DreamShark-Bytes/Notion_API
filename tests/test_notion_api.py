"""Unit tests for NotionClient version handling. No network: the client's HTTP session is replaced by a fake."""
import copy

import pytest
import requests

from notion_api import NotionClient, NOTION_VERSION

LEGACY = "2022-06-28"
DS = "2025-09-03"
LATEST = "2026-03-11"


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body, self.ok, self.text = status, body, status < 400, str(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code}", response=self)


class FakeSession:
    """routes: {(METHOD, path): body | (status, body) | [list of those, consumed in order]}"""

    def __init__(self, routes):
        self.routes, self.calls, self.headers = routes, [], {}

    def _do(self, method, url, json=None, **_):
        path = url.removeprefix("https://api.notion.com/v1")
        self.calls.append((method, path, copy.deepcopy(json)))  # copy: the client mutates payloads while paginating
        route = self.routes[(method, path)]
        if isinstance(route, list):
            route = route.pop(0)
        status, body = route if isinstance(route, tuple) else (200, route)
        return FakeResponse(status, body)

    def get(self, url, **kw): return self._do("GET", url, **kw)
    def post(self, url, **kw): return self._do("POST", url, **kw)
    def patch(self, url, **kw): return self._do("PATCH", url, **kw)
    def mount(self, *a): pass


def client(version, routes):
    c = NotionClient("token", notion_version=version)
    c._session = FakeSession(routes)
    return c


def paths(c):
    return [(m, p) for m, p, _ in c._session.calls]


def test_default_version_is_unchanged_legacy():
    c = NotionClient("token")
    assert NOTION_VERSION == LEGACY and c.notion_version == LEGACY
    assert c.headers["Notion-Version"] == LEGACY and not c.uses_data_sources


def test_header_follows_requested_version():
    assert NotionClient("token", notion_version=LATEST).headers["Notion-Version"] == LATEST


def test_legacy_query_database_uses_database_endpoint_and_paginates():
    c = client(LEGACY, {("POST", "/databases/db1/query"): [
        {"results": [1], "has_more": True, "next_cursor": "c2"}, {"results": [2], "has_more": False}]})
    assert c.query_database("db1", {"x": 1}) == [1, 2]
    assert c._session.calls[0][2] == {"page_size": 100, "filter": {"x": 1}}
    assert c._session.calls[1][2]["start_cursor"] == "c2"
    assert paths(c) == [("POST", "/databases/db1/query")] * 2


def test_data_source_id_is_used_directly_and_cached():
    c = client(DS, {("GET", "/data_sources/ds1"): {"id": "ds1"},
                    ("POST", "/data_sources/ds1/query"): {"results": [1], "has_more": False}})
    assert c.query_database("ds1") == [1]
    assert c.query_database("ds1") == [1]
    assert paths(c).count(("GET", "/data_sources/ds1")) == 1  # resolution cached


def test_database_id_resolves_to_its_single_data_source():
    c = client(DS, {("GET", "/data_sources/db1"): (404, {"code": "object_not_found"}),
                    ("GET", "/databases/db1"): {"data_sources": [{"id": "ds9", "name": "Tasks"}]},
                    ("POST", "/data_sources/ds9/query"): {"results": [7], "has_more": False}})
    assert c.query_database("db1") == [7]
    assert ("POST", "/data_sources/ds9/query") in paths(c)


def test_multi_source_database_is_ambiguous():
    c = client(DS, {("GET", "/data_sources/db1"): (404, {}),
                    ("GET", "/databases/db1"): {"data_sources": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]}})
    with pytest.raises(ValueError, match="2 data sources"):
        c.resolve_data_source_id("db1")


def test_non_lookup_errors_are_not_swallowed():
    c = client(DS, {("GET", "/data_sources/ds1"): (500, {})})
    with pytest.raises(requests.HTTPError):
        c.resolve_data_source_id("ds1")


def test_data_source_methods_refuse_legacy_version():
    c = client(LEGACY, {})
    for call in (lambda: c.get_data_source("x"), lambda: c.query_data_source("x"), lambda: c.resolve_data_source_id("x")):
        with pytest.raises(ValueError, match="requires notion_version"):
            call()


def test_get_schema_on_both_versions():
    legacy = client(LEGACY, {("GET", "/databases/db1"): {"properties": {"Name": {}}}})
    assert legacy.get_schema("db1") == {"properties": {"Name": {}}}
    new = client(DS, {("GET", "/data_sources/ds1"): {"properties": {"Name": {}}}})
    assert new.get_schema("ds1") == {"properties": {"Name": {}}}


def test_create_page_parent_shape_per_version():
    legacy = client(LEGACY, {("POST", "/pages"): {"id": "p"}})
    legacy.create_page("db1", {"Name": 1})
    assert legacy._session.calls[0][2]["parent"] == {"database_id": "db1"}
    new = client(DS, {("GET", "/data_sources/ds1"): {}, ("POST", "/pages"): {"id": "p"}})
    new.create_page("ds1", {"Name": 1})
    assert new._session.calls[-1][2]["parent"] == {"type": "data_source_id", "data_source_id": "ds1"}


def test_archive_page_key_per_version():
    for version, key in ((LEGACY, "archived"), (DS, "archived"), (LATEST, "in_trash")):
        c = client(version, {("PATCH", "/pages/p1"): {}})
        c.archive_page("p1")
        assert c._session.calls[0][2] == {key: True}


def test_create_database_schema_location_per_version():
    legacy = client(LEGACY, {("POST", "/databases"): {}})
    legacy.create_database("page", "T", {"Name": {"title": {}}})
    body = legacy._session.calls[0][2]
    assert body["properties"] == {"Name": {"title": {}}} and "initial_data_source" not in body
    new = client(DS, {("POST", "/databases"): {}})
    new.create_database("page", "T", {"Name": {"title": {}}})
    body = new._session.calls[0][2]
    assert body["initial_data_source"] == {"properties": {"Name": {"title": {}}}} and "properties" not in body


def test_patch_database_splits_schema_and_database_fields_on_new_versions():
    c = client(DS, {("GET", "/data_sources/ds1"): {}, ("PATCH", "/data_sources/ds1"): {}, ("PATCH", "/databases/ds1"): {}})
    c.patch_database("ds1", properties={"A": {}}, title="New")
    patches = [(p, b) for m, p, b in c._session.calls if m == "PATCH"]
    assert patches[0] == ("/data_sources/ds1", {"properties": {"A": {}}})
    assert patches[1][0] == "/databases/ds1" and "properties" not in patches[1][1] and "title" in patches[1][1]
    legacy = client(LEGACY, {("PATCH", "/databases/db1"): {}})
    legacy.patch_database("db1", properties={"A": {}})
    assert legacy._session.calls[0][2] == {"properties": {"A": {}}}
