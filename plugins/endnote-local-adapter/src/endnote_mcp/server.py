from __future__ import annotations

from functools import partial
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from . import endnote


mcp = FastMCP("endnote")

# Keep worker cancellation shielded: a disconnected client cannot interrupt a
# file read midway. Other sessions remain responsive while that read finishes.


@mcp.tool()
async def list_libraries() -> dict[str, Any]:
    """List configured EndNote libraries and their local availability."""
    return await anyio.to_thread.run_sync(endnote.list_libraries)


@mcp.tool()
async def search_references(
    query: str,
    library_id: str | None = None,
    fields: list[str] | None = None,
    limit: int | None = 20,
) -> dict[str, Any]:
    """Search EndNote citation metadata, excluding PDF full text."""
    return await anyio.to_thread.run_sync(
        partial(
            endnote.search_references,
            query=query,
            library_id=library_id,
            fields=fields,
            limit=limit,
        )
    )


@mcp.tool()
async def search_pdf_full_text(
    query: str,
    library_id: str | None = None,
    limit: int | None = 20,
    context_chars: int | None = 180,
) -> dict[str, Any]:
    """Search EndNote's existing indexed PDF full text."""
    return await anyio.to_thread.run_sync(
        partial(
            endnote.search_pdf_full_text,
            query=query,
            library_id=library_id,
            limit=limit,
            context_chars=context_chars,
        )
    )


@mcp.tool()
async def get_reference(
    reference_id: int, library_id: str | None = None
) -> dict[str, Any]:
    """Return full metadata and link-ready attachment summaries for one reference."""
    return await anyio.to_thread.run_sync(
        partial(endnote.get_reference, reference_id=reference_id, library_id=library_id)
    )


@mcp.tool()
async def list_attachments(
    reference_id: int, library_id: str | None = None
) -> dict[str, Any]:
    """List resolved attachment paths, file URIs, and Markdown links."""
    return await anyio.to_thread.run_sync(
        partial(
            endnote.list_attachments, reference_id=reference_id, library_id=library_id
        )
    )


@mcp.tool()
async def extract_pdf_text(
    reference_id: int,
    attachment_index: int | None = 0,
    library_id: str | None = None,
    max_chars: int | None = 4000,
) -> dict[str, Any]:
    """Extract text from an attached PDF on demand."""
    return await anyio.to_thread.run_sync(
        partial(
            endnote.extract_pdf_text,
            reference_id=reference_id,
            attachment_index=attachment_index,
            library_id=library_id,
            max_chars=max_chars,
        )
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
