"""Web surfaces: template injection and JS syntax (node --check when present).

A page whose script fails to parse renders chrome but never boots — exactly
the failure node --check catches and pytest alone can't see.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from tvguide.stream import mobile, web

node = shutil.which("node")
needs_node = pytest.mark.skipif(node is None, reason="node not installed")


def _script(page: str) -> str:
    assert "<script>" in page and "</script>" in page
    return page.split("<script>", 1)[1].split("</script>", 1)[0]


def test_desktop_page_injects_tokens():
    page = web.render_app("cinema")
    assert "__TOKENS__" not in page and "__INITIAL__" not in page
    assert "--accent:#36e0c8" in page and '"cinema"' in page


def test_mobile_page_injects_tokens():
    page = mobile.render_mobile()
    assert "__TOKENS__" not in page and "__ERA__" not in page
    assert "--accent:#36e0c8" in page and "tab-binge" in page


@needs_node
def test_desktop_page_js_parses(tmp_path):
    js = tmp_path / "web.js"
    js.write_text(_script(web.render_app(None)))
    subprocess.run([node, "--check", str(js)], check=True)


@needs_node
def test_mobile_page_js_parses(tmp_path):
    js = tmp_path / "mobile.js"
    js.write_text(_script(mobile.render_mobile()))
    subprocess.run([node, "--check", str(js)], check=True)
