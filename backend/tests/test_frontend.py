# The page itself: that it's served at its addresses, and that nothing is left
# untranslated. The French list is easy to forget, so a test keeps it honest.

import pathlib
import re
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).parent.parent
FRONTEND = ROOT / "frontend"
FR_SOURCE = (FRONTEND / "js/core/fr.js").read_text(encoding="utf-8")
JS_FILES = [p for p in (FRONTEND / "js").rglob("*.js") if p.name != "fr.js"]
JS_SOURCE = "\n".join(p.read_text(encoding="utf-8") for p in JS_FILES)
# Same in both languages, or not language at all.
NOT_TRANSLATED = {"Receipt Vault", "graloop", "gral", "p", "×", "English", "Français"}


def french_has(text: str) -> bool:
    bare = text.replace("\\", "")
    keys = re.findall(r'^\s*(?:"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|(\w+)):', FR_SOURCE, re.M)
    return any(bare == (a or b or c).replace("\\", "") for a, b, c in keys)


class VisibleText(HTMLParser):
    SKIPPED = {"script", "style", "svg"}

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.found = set()

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIPPED:
            self.depth += 1
        for name, value in attrs:
            if name in ("placeholder", "aria-label", "title", "alt") and value:
                self.found.add(value)

    def handle_endtag(self, tag):
        if tag in self.SKIPPED:
            self.depth -= 1

    def handle_data(self, data):
        text = " ".join(data.split())
        if text and not self.depth:
            self.found.add(text)


def test_every_index_html_text_has_french():
    parser = VisibleText()
    parser.feed((FRONTEND / "index.html").read_text(encoding="utf-8"))
    missing = {text for text in parser.found - NOT_TRANSLATED if not french_has(text)}
    assert not missing


def test_every_script_string_has_french():
    strings = re.findall(r"\bt\(\s*(?:\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)')", JS_SOURCE)
    assert strings
    missing = {a or b for a, b in strings if not french_has(a or b)}
    assert not missing


def test_every_server_message_has_french():
    source = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "app").rglob("*.py"))
    messages = re.findall(r'(?:ExtractionError|FileRejected)\("([^"]+)"\)', source)
    messages += re.findall(r'HTTP_\w+, "([^"]+)"\)', source)
    assert len(messages) > 10
    missing = {m for m in messages if not french_has(m)}
    assert not missing


def test_index_is_served_with_versioned_assets(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Receipt Vault" in response.text
    assert re.search(r'src="/js/main\.js\?v=\d+"', response.text)
    assert re.search(r'href="/css/base\.css\?v=\d+"', response.text)


def test_receipt_address_opens_the_same_page(client):
    assert client.get("/receipt/5").text == client.get("/").text


def test_the_graloop_logo_is_at_the_top(client):
    html = client.get("/").text
    assert '<span class="logo" role="img" aria-label="graloop">gral<svg' in html
    assert html.index('class="logo"') < html.index("<main>")


def test_scripts_and_styles_are_served(client):
    for path in ("/js/main.js", "/css/base.css", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon.svg"):
        assert client.get(path).status_code == 200, path


def test_every_module_a_script_imports_exists():
    for script in JS_FILES:
        for target in re.findall(r'(?:from |import )"(\.[^"]+)"', script.read_text(encoding="utf-8")):
            assert (script.parent / target).resolve().exists(), f"{script.name} -> {target}"


def test_scripts_never_build_html_from_strings():
    # A receipt's text comes from outside: it must only ever go in as text.
    assert "innerHTML" not in JS_SOURCE
    assert "insertAdjacentHTML" not in JS_SOURCE


def test_api_docs_are_off_by_default(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}
