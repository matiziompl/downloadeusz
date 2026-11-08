"""Documentation uses versioned local files and is safe to insert into the UI."""

from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from backend import main


client = TestClient(main.app)


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.items = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))


@pytest.mark.parametrize("section", ["readme", "latest", "instructions"])
def test_shipped_documentation_is_available(section):
    response = client.get(f"/api/documentation/{section}")
    assert response.status_code == 200
    document = response.json()
    assert document["version"] == main.APP_VERSION
    assert any(tag == "h1" for tag, _ in Elements(document["html"]).items)
    assert main.APP_VERSION == client.get("/api/version").json()["version"] == main.app.version


@pytest.fixture
def documents(tmp_path, monkeypatch):
    from backend import documentation

    monkeypatch.setattr(documentation, "PROJECT_ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("# README testowe", encoding="utf-8")
    (tmp_path / "docs" / "INSTRUCTION.md").write_text("# Instrukcja testowa", encoding="utf-8")
    (tmp_path / "docs" / f"v{main.APP_VERSION}.md").write_text("# Bieżące zmiany", encoding="utf-8")
    return tmp_path


def test_latest_document_is_selected_by_running_version(documents):
    (documents / "docs" / "v9.99.md").write_text("# Przyszła wersja", encoding="utf-8")
    response = client.get("/api/documentation/latest")
    assert response.status_code == 200
    assert "Bieżące zmiany" in response.json()["html"]
    assert "Przyszła wersja" not in response.json()["html"]


def test_markdown_preserves_structured_readable_content(documents):
    (documents / "README.md").write_text(
        "# Nagłówek\n\n**Ważne** i [pomoc](https://example.org).\n\n"
        "| Opcja | Opis |\n| --- | --- |\n| FLAC | Audio |\n\n"
        "```bash\necho '<plik>'\n```\n",
        encoding="utf-8",
    )
    response = client.get("/api/documentation/readme")
    assert response.status_code == 200
    html = response.json()["html"]
    elements = Elements(html).items
    assert {"h1", "strong", "a", "table", "th", "td", "pre", "code"} <= {tag for tag, _ in elements}
    assert ("a", {"href": "https://example.org"}) in elements
    assert "&lt;plik&gt;" in html


def test_markdown_cannot_insert_active_html_or_unsafe_urls(documents):
    (documents / "README.md").write_text(
        '<script>alert(1)</script>\n\n<img src=x onerror="alert(1)">\n\n'
        '[link](javascript:alert%281%29)\n\n![svg](data:image/svg+xml,evil)\n',
        encoding="utf-8",
    )
    response = client.get("/api/documentation/readme")
    assert response.status_code == 200
    elements = Elements(response.json()["html"]).items
    assert not any(tag in {"script", "img"} for tag, _ in elements)
    assert not any("javascript:" in attrs.get("href", "") for _, attrs in elements)
    assert not any(any(name.startswith("on") for name in attrs) for _, attrs in elements)


def test_missing_document_reports_unavailable_without_breaking_other_tabs(documents):
    (documents / "docs" / f"v{main.APP_VERSION}.md").unlink()
    response = client.get("/api/documentation/latest")
    assert response.status_code == 503
    assert response.json()["detail"]
    assert str(documents) not in response.text
    assert client.get("/api/documentation/readme").status_code == 200
    assert client.get("/api/documentation/instructions").status_code == 200


def test_unreadable_document_reports_unavailable(documents):
    readme = documents / "README.md"
    readme.unlink()
    readme.mkdir()
    response = client.get("/api/documentation/readme")
    assert response.status_code == 503
    assert response.json()["detail"]
    assert str(documents) not in response.text


@pytest.mark.parametrize("section", ["private", "README.md", "..%2F..%2Fetc%2Fpasswd"])
def test_unknown_document_does_not_allow_arbitrary_file_access(section):
    response = client.get(f"/api/documentation/{section}")
    assert response.status_code == 404
