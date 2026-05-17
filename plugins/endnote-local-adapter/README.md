# MCP Adapter for EndNote (Unofficial)

Unofficial read-only MCP adapter for user-configured local EndNote libraries.

## Legal and licensing notes

This project is not affiliated with, endorsed by, sponsored by, or supported by
Clarivate. EndNote is a trademark of Clarivate or its affiliates. This adapter
is intended for use with a licensed local EndNote installation and libraries
that the user is authorized to access.

This repository does not include EndNote software, documentation, output styles,
filters, connection files, RDK materials, EndNote libraries, PDF indexes, or PDF
attachments. Users are responsible for complying with their EndNote license,
institutional terms, and the rights attached to any PDFs or other third-party
materials they access through this adapter.

The adapter currently reads user-configured local library and index files in
read-only mode. For commercial, broad public, or tightly integrated EndNote
extensions, prefer Clarivate's official RSServices API/RDK route and obtain any
required Clarivate permissions.

Useful references:

- [EndNote license agreement](https://endnote.com/license/)
- [EndNote terms of use](https://endnote.com/terms-of-use/)
- [EndNote APIs and plug-ins documentation](https://docs.endnote.com/docs/endnote/2025/v1/windows/en/content/appendices/apis_and_plug-ins.htm)

## Installation for Codex users

### Install from GitHub marketplace

1. In Codex, open Plugins, then Add marketplace.
2. Enter `https://github.com/kmnhan/endnote-mcp.git`.
   Do not use the SSH shorthand `git@github.com:kmnhan/endnote-mcp`; Codex
   fetches marketplaces non-interactively, so SSH host-key or key prompts can
   make the add fail before the marketplace is read.
3. Install or enable `MCP Adapter for EndNote (Unofficial)`.
4. Install `uv` if it is not already available on your machine.
5. Copy `config/libraries.example.json` to `config/libraries.local.json`.
6. Edit `config/libraries.local.json` so each `path` points to a local `.enl`
   library that you are authorized to access.

### Install from a local checkout

1. Clone this repository.
2. Open the repository folder in Codex.
3. Install `uv` if it is not already available on your machine.
4. In Codex, install or enable `MCP Adapter for EndNote (Unofficial)` from the
   local plugin marketplace. This repository declares it in
   `.agents/plugins/marketplace.json`.
5. Copy `config/libraries.example.json` to `config/libraries.local.json`.
6. Edit `config/libraries.local.json` so each `path` points to a local `.enl`
   library that you are authorized to access.

For a home-local Codex install, copy this plugin directory to
`~/plugins/endnote-local-adapter` and add a marketplace entry at
`~/.agents/plugins/marketplace.json` whose `source.path` is
`./plugins/endnote-local-adapter`.

`libraries.local.json` is ignored by git so each user can keep their own
absolute EndNote paths out of the repository.

## Tools

- `list_libraries`: show configured libraries and whether their `.enl`, `.Data`, and PDF index paths exist.
- `search_references`: search EndNote metadata fields in the `.enl` SQLite database.
- `search_pdf_full_text`: search EndNote's existing PDF text index at `<library>.Data/sdb/pdb.eni`.
- `get_reference`: return full metadata and attachment summaries for one reference.
- `list_attachments`: resolve EndNote attachment paths under `<library>.Data/PDF`.
- `extract_pdf_text`: read an attached PDF on demand.

All tools are read-only.

## Development

```bash
uv run --group dev python -m pytest
uv run python scripts/smoke_test.py
```
