"""Narrow compatibility adapter for twscrape's X client transaction parser."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Pattern


LOGGER = logging.getLogger(__name__)
DIRECT_LEGACY_ASSET_RE = re.compile(
    r"https://abs\.twimg\.com/responsive-web/client-web/[A-Za-z0-9_./-]+\.js"
)
PATCH_MARKER = "_ai_news_inline_indices_compat"

Parser = Callable[[str, Any], Awaitable[list[int]]]
PageLoader = Callable[[str, Any], Awaitable[str]]


def extract_direct_legacy_assets(html: str) -> list[str]:
    """Return trusted direct legacy bundles, with the main bundle first."""
    urls = list(dict.fromkeys(DIRECT_LEGACY_ASSET_RE.findall(html)))
    return sorted(
        urls,
        key=lambda url: 0 if url.rsplit("/", 1)[-1].startswith("main.") else 1,
    )


async def load_trusted_asset(url: str, client: Any) -> str:
    """Load one trusted bundle without following redirects to another origin."""
    backend = str(getattr(client, "backend", ""))
    if backend == "httpx":
        response = await client.get(url, follow_redirects=False)
    elif backend == "curl":
        response = await client.get(url, allow_redirects=False)
    else:
        raise ValueError(f"Unsupported twscrape HTTP backend: {backend or 'unknown'}")

    if 300 <= response.status_code < 400:
        raise ValueError("XClId compatibility asset redirect refused")
    response.raise_for_status()
    if DIRECT_LEGACY_ASSET_RE.fullmatch(str(response.url)) is None:
        raise ValueError("XClId compatibility asset resolved to an untrusted URL")
    return str(response.text)


def build_compatible_parser(
    original_parser: Parser,
    get_page_text: PageLoader,
    indices_regex: Pattern[str],
) -> Parser:
    """Build a parser that reads indices inlined into trusted legacy bundles."""

    async def parse_anim_idx(html: str, client: Any) -> list[int]:
        for url in extract_direct_legacy_assets(html):
            try:
                script = await get_page_text(url, client)
            except Exception as exc:
                LOGGER.warning(
                    "XClId compatibility asset %s failed: %s",
                    url.rsplit("/", 1)[-1],
                    type(exc).__name__,
                )
                continue

            try:
                indices = [int(match.group(2)) for match in indices_regex.finditer(script)]
            except (IndexError, ValueError):
                LOGGER.warning("XClId compatibility index pattern is incompatible")
                break
            if indices:
                LOGGER.info(
                    "XClId compatibility used inline indices from %s",
                    url.rsplit("/", 1)[-1],
                )
                return indices

        return await original_parser(html, client)

    return parse_anim_idx


def install_twscrape_xclid_compat() -> None:
    """Install the adapter once without importing twscrape at module import time."""
    import twscrape.xclid as xclid

    if getattr(xclid, PATCH_MARKER, False):
        return

    xclid.parse_anim_idx = build_compatible_parser(
        xclid.parse_anim_idx,
        load_trusted_asset,
        xclid.INDICES_REGEX,
    )
    setattr(xclid, PATCH_MARKER, True)
    LOGGER.info("Installed XClId inline-index compatibility adapter")
