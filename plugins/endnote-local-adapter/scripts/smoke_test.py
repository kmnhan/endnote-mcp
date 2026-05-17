from __future__ import annotations

from endnote_mcp import endnote


def main() -> None:
    libraries = endnote.list_libraries()
    print(f"configured_libraries={len(libraries['libraries'])}")
    default_library = libraries["default_library_id"]
    print(f"default_library={default_library}")

    metadata = endnote.search_references("magnetic", limit=3)
    print(f"metadata_results={metadata['count']}")
    if metadata["results"]:
        first_id = metadata["results"][0]["id"]
        detail = endnote.get_reference(first_id)
        print(f"first_reference_id={first_id}")
        print(f"first_attachment_count={len(detail['attachments'])}")

    pdf = endnote.search_pdf_full_text("magnetic", limit=3, context_chars=80)
    print(f"pdf_results={pdf['count']}")


if __name__ == "__main__":
    main()

