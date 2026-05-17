from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import endnote


mcp = FastMCP("endnote")


@mcp.tool()
def list_libraries() -> dict[str, Any]:
    """List configured EndNote libraries and their local availability."""
    return endnote.list_libraries()


@mcp.tool()
def search_references(
    query: str,
    library_id: str | None = None,
    fields: list[str] | None = None,
    limit: int | None = 20,
) -> dict[str, Any]:
    """Search EndNote citation metadata, excluding PDF full text."""
    return endnote.search_references(
        query=query,
        library_id=library_id,
        fields=fields,
        limit=limit,
    )


@mcp.tool()
def search_pdf_full_text(
    query: str,
    library_id: str | None = None,
    limit: int | None = 20,
    context_chars: int | None = 180,
) -> dict[str, Any]:
    """Search EndNote's existing indexed PDF full text."""
    return endnote.search_pdf_full_text(
        query=query,
        library_id=library_id,
        limit=limit,
        context_chars=context_chars,
    )


@mcp.tool()
def get_reference(reference_id: int, library_id: str | None = None) -> dict[str, Any]:
    """Return full metadata and link-ready attachment summaries for one reference."""
    return endnote.get_reference(reference_id=reference_id, library_id=library_id)


@mcp.tool()
def list_attachments(reference_id: int, library_id: str | None = None) -> dict[str, Any]:
    """List resolved attachment paths, file URIs, and Markdown links."""
    return endnote.list_attachments(reference_id=reference_id, library_id=library_id)


@mcp.tool()
def extract_pdf_text(
    reference_id: int,
    attachment_index: int | None = 0,
    library_id: str | None = None,
    max_chars: int | None = 4000,
) -> dict[str, Any]:
    """Extract text from an attached PDF on demand."""
    return endnote.extract_pdf_text(
        reference_id=reference_id,
        attachment_index=attachment_index,
        library_id=library_id,
        max_chars=max_chars,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
