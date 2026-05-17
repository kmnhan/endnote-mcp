---
name: endnote
description: Use when the user asks to search, inspect, cite, or extract information from configured local EndNote libraries through the unofficial EndNote MCP adapter.
---

# EndNote Local Adapter

Use the unofficial EndNote MCP tools for configured local EndNote libraries.

- Use `list_libraries` before library-specific work when the target library is unclear.
- Use `search_references` for citation metadata fields only.
- Use `search_pdf_full_text` when the user asks whether attached PDFs mention a topic or phrase.
- Use `get_reference` before citing or summarizing one specific record.
- Use `list_attachments` before referring to local PDF paths.
- When presenting attachments in Codex Desktop, show `markdown_link` and
  `markdown_parent_link` instead of raw local paths when those fields are
  available.
- Use `extract_pdf_text` only when the user explicitly needs PDF contents, because it reads the attached PDF.

All tools are read-only. Do not promise library writes, imports, deletes, or synchronization.
