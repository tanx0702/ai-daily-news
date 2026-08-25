import asyncio
import re
import sys
from types import ModuleType

from scripts.twscrape_xclid_compat import (
    build_compatible_parser,
    extract_direct_legacy_assets,
    install_twscrape_xclid_compat,
)


AUTHENTICATED_HTML = """
<script src="https://abs.twimg.com/responsive-web/client-web/vendor.bb0ab181e93eb4a1a.js"></script>
<script src="https://abs.twimg.com/responsive-web/client-web/i18n/en.c085ee655698e5e1a.js"></script>
<script src="https://abs.twimg.com/responsive-web/client-web/main.3fc0640facfee243a.js"></script>
<script src="https://assets.example.com/responsive-web/client-web/main.evil.js"></script>
<script src="https://abs.twimg.com/x-web/x-web/entry-client-logged-out-test.js"></script>
"""
INDICES_REGEX = re.compile(r"(index\[(\d+)\])")


def test_extract_direct_legacy_assets_accepts_only_trusted_bundles_and_prioritizes_main():
    assert extract_direct_legacy_assets(AUTHENTICATED_HTML) == [
        "https://abs.twimg.com/responsive-web/client-web/main.3fc0640facfee243a.js",
        "https://abs.twimg.com/responsive-web/client-web/vendor.bb0ab181e93eb4a1a.js",
        "https://abs.twimg.com/responsive-web/client-web/i18n/en.c085ee655698e5e1a.js",
    ]


def test_compatible_parser_returns_inline_indices_without_calling_upstream():
    calls = []

    async def original_parser(_html, _client):
        calls.append("original")
        return [9]

    async def get_page_text(url, _client):
        calls.append(url.rsplit("/", 1)[-1])
        return "prefix index[5] suffix"

    parser = build_compatible_parser(original_parser, get_page_text, INDICES_REGEX)

    assert asyncio.run(parser(AUTHENTICATED_HTML, object())) == [5]
    assert calls == ["main.3fc0640facfee243a.js"]


def test_compatible_parser_delegates_when_direct_assets_have_no_inline_indices():
    calls = []

    async def original_parser(_html, _client):
        calls.append("original")
        return [7]

    async def get_page_text(url, _client):
        calls.append(url.rsplit("/", 1)[-1])
        return "no animation indices"

    parser = build_compatible_parser(original_parser, get_page_text, INDICES_REGEX)

    assert asyncio.run(parser(AUTHENTICATED_HTML, object())) == [7]
    assert calls == [
        "main.3fc0640facfee243a.js",
        "vendor.bb0ab181e93eb4a1a.js",
        "en.c085ee655698e5e1a.js",
        "original",
    ]


def test_compatible_parser_continues_after_one_asset_request_fails():
    async def original_parser(_html, _client):
        raise AssertionError("upstream parser should not be called")

    async def get_page_text(url, _client):
        if "/main." in url:
            raise TimeoutError("simulated asset timeout")
        return "index[3]"

    parser = build_compatible_parser(original_parser, get_page_text, INDICES_REGEX)

    assert asyncio.run(parser(AUTHENTICATED_HTML, object())) == [3]


def test_installer_is_idempotent(monkeypatch):
    async def original_parser(_html, _client):
        return [1]

    async def get_page_text(_url, _client):
        return "index[2]"

    package = ModuleType("twscrape")
    package.__path__ = []
    xclid = ModuleType("twscrape.xclid")
    xclid.parse_anim_idx = original_parser
    xclid.get_tw_page_text = get_page_text
    xclid.INDICES_REGEX = INDICES_REGEX
    package.xclid = xclid
    monkeypatch.setitem(sys.modules, "twscrape", package)
    monkeypatch.setitem(sys.modules, "twscrape.xclid", xclid)

    install_twscrape_xclid_compat()
    installed = xclid.parse_anim_idx
    install_twscrape_xclid_compat()

    assert xclid.parse_anim_idx is installed
    assert asyncio.run(installed(AUTHENTICATED_HTML, object())) == [2]
