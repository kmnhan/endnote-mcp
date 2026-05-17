from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = PLUGIN_ROOT / "config"
LOCAL_CONFIG_PATH = DEFAULT_CONFIG_DIR / "libraries.local.json"
USER_CONFIG_PATH = Path.home() / ".config" / "endnote-mcp" / "libraries.local.json"
EXAMPLE_CONFIG_PATH = DEFAULT_CONFIG_DIR / "libraries.example.json"
CONFIG_ENV_VAR = "ENDNOTE_MCP_CONFIG"

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
DEFAULT_CONTEXT_CHARS = 180
MAX_CONTEXT_CHARS = 1200

REFERENCE_FIELDS = [
    "id",
    "trash_state",
    "reference_type",
    "author",
    "year",
    "title",
    "pages",
    "secondary_title",
    "volume",
    "number",
    "publisher",
    "keywords",
    "date",
    "abstract",
    "url",
    "notes",
    "isbn",
    "short_title",
    "electronic_resource_number",
    "language",
    "added_to_library",
    "record_last_updated",
    "read_status",
    "rating",
]

SEARCH_FIELD_MAP = {
    "title": "title",
    "author": "author",
    "year": "year",
    "journal": "secondary_title",
    "secondary_title": "secondary_title",
    "abstract": "abstract",
    "keywords": "keywords",
    "notes": "notes",
    "url": "url",
    "doi": "electronic_resource_number",
    "isbn": "isbn",
    "publisher": "publisher",
}

DEFAULT_SEARCH_FIELDS = [
    "title",
    "author",
    "year",
    "journal",
    "abstract",
    "keywords",
    "notes",
    "url",
    "doi",
]


class EndNoteError(ValueError):
    """Raised for user-correctable EndNote plugin errors."""


@dataclass(frozen=True)
class Library:
    id: str
    name: str
    path: Path

    @property
    def data_dir(self) -> Path:
        return self.path.with_suffix(".Data")

    @property
    def pdf_dir(self) -> Path:
        return self.data_dir / "PDF"

    @property
    def pdf_index_path(self) -> Path:
        return self.data_dir / "sdb" / "pdb.eni"

    def to_summary(self, *, is_default: bool) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "path": str(self.path),
            "default": is_default,
            "exists": self.path.exists(),
            "data_dir": str(self.data_dir),
            "data_dir_exists": self.data_dir.exists(),
            "pdf_index": str(self.pdf_index_path),
            "pdf_index_exists": self.pdf_index_path.exists(),
        }


@dataclass(frozen=True)
class LibraryConfig:
    default_library_id: str
    libraries: dict[str, Library]
    source_path: Path


def load_config(config_path: Path | None = None) -> LibraryConfig:
    if config_path is not None:
        path = config_path
    elif env_config := os.environ.get(CONFIG_ENV_VAR):
        path = Path(env_config).expanduser()
        if not path.exists():
            raise EndNoteError(f"{CONFIG_ENV_VAR} points to missing config: {path}")
    else:
        path = next(
            (
                candidate
                for candidate in (LOCAL_CONFIG_PATH, USER_CONFIG_PATH, EXAMPLE_CONFIG_PATH)
                if candidate.exists()
            ),
            LOCAL_CONFIG_PATH,
        )
    if not path.exists():
        raise EndNoteError(
            f"No EndNote library config found. Create {USER_CONFIG_PATH} or "
            f"{LOCAL_CONFIG_PATH} from {EXAMPLE_CONFIG_PATH}."
        )

    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    raw_libraries = payload.get("libraries")
    if not isinstance(raw_libraries, list) or not raw_libraries:
        raise EndNoteError(f"{path} must contain a non-empty 'libraries' array.")

    libraries: dict[str, Library] = {}
    for raw_library in raw_libraries:
        if not isinstance(raw_library, dict):
            raise EndNoteError(f"{path} contains a non-object library entry.")
        library_id = str(raw_library.get("id", "")).strip()
        name = str(raw_library.get("name", library_id)).strip() or library_id
        raw_path = str(raw_library.get("path", "")).strip()
        if not library_id:
            raise EndNoteError(f"{path} contains a library without an id.")
        if library_id in libraries:
            raise EndNoteError(f"{path} contains duplicate library id '{library_id}'.")
        if not raw_path:
            raise EndNoteError(f"{path} library '{library_id}' does not define a path.")
        libraries[library_id] = Library(
            id=library_id,
            name=name,
            path=Path(raw_path).expanduser(),
        )

    default_library_id = str(payload.get("defaultLibraryId", "")).strip()
    if not default_library_id:
        default_library_id = next(iter(libraries))
    if default_library_id not in libraries:
        raise EndNoteError(
            f"{path} defaultLibraryId '{default_library_id}' is not listed in libraries."
        )

    return LibraryConfig(
        default_library_id=default_library_id,
        libraries=libraries,
        source_path=path,
    )


def get_library(library_id: str | None = None, config: LibraryConfig | None = None) -> Library:
    config = config or load_config()
    resolved_id = (library_id or config.default_library_id).strip()
    try:
        library = config.libraries[resolved_id]
    except KeyError as exc:
        known = ", ".join(sorted(config.libraries))
        raise EndNoteError(f"Unknown library_id '{resolved_id}'. Known libraries: {known}.") from exc
    if not library.path.exists():
        raise EndNoteError(f"EndNote library does not exist: {library.path}")
    if library.path.suffix.lower() != ".enl":
        raise EndNoteError(f"EndNote library path must point to a .enl file: {library.path}")
    return library


def list_libraries() -> dict[str, Any]:
    config = load_config()
    return {
        "default_library_id": config.default_library_id,
        "config_path": str(config.source_path),
        "libraries": [
            library.to_summary(is_default=library.id == config.default_library_id)
            for library in config.libraries.values()
        ],
    }


def open_sqlite_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise EndNoteError(f"SQLite database does not exist: {path}")
    uri = f"file:{quote(str(path), safe='/')}?mode=ro&nolock=1"
    conn = sqlite3.connect(uri, uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    return conn


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), MAX_LIMIT))


def clamp_context_chars(context_chars: int | None) -> int:
    if context_chars is None:
        return DEFAULT_CONTEXT_CHARS
    return max(40, min(int(context_chars), MAX_CONTEXT_CHARS))


def normalize_query(query: str) -> str:
    normalized = " ".join(str(query).split())
    if not normalized:
        raise EndNoteError("Query must not be empty.")
    return normalized


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_pattern(query: str) -> str:
    return f"%{escape_like(query)}%"


def resolve_search_fields(fields: list[str] | None) -> list[str]:
    requested = fields or DEFAULT_SEARCH_FIELDS
    resolved: list[str] = []
    unknown: list[str] = []
    for field in requested:
        normalized = str(field).strip()
        column = SEARCH_FIELD_MAP.get(normalized)
        if column is None:
            unknown.append(normalized)
        elif column not in resolved:
            resolved.append(column)
    if unknown:
        allowed = ", ".join(sorted(SEARCH_FIELD_MAP))
        raise EndNoteError(f"Unknown search field(s): {', '.join(unknown)}. Allowed: {allowed}.")
    return resolved


def reference_summary(row: sqlite3.Row, *, matched_fields: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "title": row["title"],
        "short_title": row["short_title"],
        "author": row["author"],
        "year": row["year"],
        "journal": row["secondary_title"],
        "reference_type": int(row["reference_type"]),
        "doi": row["electronic_resource_number"],
        "url": row["url"],
        "keywords": row["keywords"],
        "abstract": truncate(row["abstract"], 500),
        "matched_fields": matched_fields or [],
    }


def reference_detail(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "reference_type": int(row["reference_type"]),
        "author": row["author"],
        "year": row["year"],
        "title": row["title"],
        "short_title": row["short_title"],
        "journal": row["secondary_title"],
        "volume": row["volume"],
        "number": row["number"],
        "pages": row["pages"],
        "publisher": row["publisher"],
        "date": row["date"],
        "abstract": row["abstract"],
        "keywords": row["keywords"],
        "notes": row["notes"],
        "url": row["url"],
        "doi": row["electronic_resource_number"],
        "isbn": row["isbn"],
        "language": row["language"],
        "read_status": row["read_status"],
        "rating": row["rating"],
        "added_to_library": row["added_to_library"],
        "record_last_updated": row["record_last_updated"],
    }


def truncate(value: str | None, max_chars: int) -> str:
    if not value:
        return ""
    value = str(value)
    if len(value) <= max_chars:
        return value
    return value[: max(0, max_chars - 1)].rstrip() + "..."


def select_reference(conn: sqlite3.Connection, reference_id: int) -> sqlite3.Row:
    columns = ", ".join(REFERENCE_FIELDS)
    row = conn.execute(
        f"SELECT {columns} FROM refs WHERE id = ? AND trash_state = 0",
        (int(reference_id),),
    ).fetchone()
    if row is None:
        raise EndNoteError(f"Reference not found or is in trash: {reference_id}")
    return row


def attachment_count(conn: sqlite3.Connection, reference_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM file_res WHERE refs_id = ?",
        (int(reference_id),),
    ).fetchone()
    return int(row["count"])


def attachment_rows(conn: sqlite3.Connection, reference_id: int) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT refs_id, file_path, file_type, file_pos
        FROM file_res
        WHERE refs_id = ?
        ORDER BY file_pos, file_path
        """,
        (int(reference_id),),
    ).fetchall()
    return list(rows)


def resolve_attachment_path(library: Library, raw_file_path: str) -> Path:
    raw_path = Path(raw_file_path)
    if raw_path.is_absolute():
        return raw_path
    pdf_candidate = library.pdf_dir / raw_path
    if pdf_candidate.exists():
        return pdf_candidate
    data_candidate = library.data_dir / raw_path
    if data_candidate.exists():
        return data_candidate
    return pdf_candidate


def absolute_path(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded
    return expanded.absolute()


def markdown_local_link(label: str, path: Path) -> str:
    return f"[{label}](<{path}>)"


def attachment_links(path: Path) -> dict[str, str]:
    absolute = absolute_path(path)
    parent = absolute.parent
    return {
        "file_uri": absolute.as_uri(),
        "parent_path": str(parent),
        "parent_uri": parent.as_uri(),
        "markdown_link": markdown_local_link("Open PDF", absolute),
        "markdown_parent_link": markdown_local_link("Show containing folder", parent),
    }


def format_attachment(library: Library, row: sqlite3.Row, index: int) -> dict[str, Any]:
    resolved = resolve_attachment_path(library, row["file_path"])
    return {
        "index": index,
        "reference_id": int(row["refs_id"]),
        "file_path": row["file_path"],
        "resolved_path": str(resolved),
        **attachment_links(resolved),
        "exists": resolved.exists(),
        "file_type": int(row["file_type"]),
        "file_pos": int(row["file_pos"]),
    }


def list_attachments(reference_id: int, library_id: str | None = None) -> dict[str, Any]:
    library = get_library(library_id)
    with open_sqlite_readonly(library.path) as conn:
        select_reference(conn, int(reference_id))
        attachments = [
            format_attachment(library, row, index)
            for index, row in enumerate(attachment_rows(conn, int(reference_id)))
        ]
    return {
        "library_id": library.id,
        "reference_id": int(reference_id),
        "count": len(attachments),
        "attachments": attachments,
    }


def get_reference(reference_id: int, library_id: str | None = None) -> dict[str, Any]:
    library = get_library(library_id)
    with open_sqlite_readonly(library.path) as conn:
        row = select_reference(conn, int(reference_id))
        attachments = [
            format_attachment(library, attachment, index)
            for index, attachment in enumerate(attachment_rows(conn, int(reference_id)))
        ]
    return {
        "library_id": library.id,
        "reference": reference_detail(row),
        "attachments": attachments,
    }


def matched_fields_for_row(row: sqlite3.Row, columns: list[str], query: str) -> list[str]:
    query_lower = query.casefold()
    matched: list[str] = []
    reverse_map = {column: public for public, column in SEARCH_FIELD_MAP.items()}
    for column in columns:
        value = row[column]
        if value and query_lower in str(value).casefold():
            matched.append(reverse_map.get(column, column))
    return matched


def search_references(
    query: str,
    library_id: str | None = None,
    fields: list[str] | None = None,
    limit: int | None = DEFAULT_LIMIT,
) -> dict[str, Any]:
    query = normalize_query(query)
    library = get_library(library_id)
    columns = resolve_search_fields(fields)
    where = " OR ".join([f"{column} LIKE ? ESCAPE '\\'" for column in columns])
    params: list[Any] = [like_pattern(query) for _ in columns]
    max_results = clamp_limit(limit)
    select_columns = ", ".join(REFERENCE_FIELDS)

    with open_sqlite_readonly(library.path) as conn:
        rows = conn.execute(
            f"""
            SELECT {select_columns}
            FROM refs
            WHERE trash_state = 0 AND ({where})
            ORDER BY record_last_updated DESC, id DESC
            LIMIT ?
            """,
            (*params, max_results),
        ).fetchall()
        results = []
        for row in rows:
            summary = reference_summary(
                row,
                matched_fields=matched_fields_for_row(row, columns, query),
            )
            summary["attachment_count"] = attachment_count(conn, int(row["id"]))
            results.append(summary)

    return {
        "library_id": library.id,
        "query": query,
        "fields": fields or DEFAULT_SEARCH_FIELDS,
        "count": len(results),
        "results": results,
    }


def make_snippet(contents: str, query: str, context_chars: int) -> str:
    normalized_query = query.casefold()
    lower_contents = contents.casefold()
    position = lower_contents.find(normalized_query)
    if position < 0:
        for token in re.findall(r"\w+", normalized_query):
            position = lower_contents.find(token)
            if position >= 0:
                break
    if position < 0:
        position = 0

    start = max(0, position - context_chars)
    end = min(len(contents), position + len(query) + context_chars)
    snippet = contents[start:end]
    snippet = re.sub(r"\s+", " ", snippet).strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(contents):
        snippet = snippet + "..."
    return snippet


def search_pdf_full_text(
    query: str,
    library_id: str | None = None,
    limit: int | None = DEFAULT_LIMIT,
    context_chars: int | None = DEFAULT_CONTEXT_CHARS,
) -> dict[str, Any]:
    query = normalize_query(query)
    library = get_library(library_id)
    if not library.pdf_index_path.exists():
        raise EndNoteError(f"EndNote PDF index not found: {library.pdf_index_path}")

    max_results = clamp_limit(limit)
    context = clamp_context_chars(context_chars)
    rows_to_fetch = max_results * 5
    grouped: dict[int, list[dict[str, Any]]] = {}

    with open_sqlite_readonly(library.pdf_index_path) as pdf_conn:
        pdf_rows = pdf_conn.execute(
            """
            SELECT refs_id, subkey, contents
            FROM pdf_index
            WHERE contents LIKE ? ESCAPE '\\'
            ORDER BY refs_id
            LIMIT ?
            """,
            (like_pattern(query), rows_to_fetch),
        ).fetchall()

    with open_sqlite_readonly(library.path) as library_conn:
        for pdf_row in pdf_rows:
            refs_id = int(pdf_row["refs_id"])
            try:
                ref_row = select_reference(library_conn, refs_id)
            except EndNoteError:
                continue
            grouped.setdefault(refs_id, []).append(
                {
                    "subkey": str(pdf_row["subkey"]),
                    "snippet": make_snippet(pdf_row["contents"], query, context),
                }
            )
            if len(grouped) >= max_results:
                break

        results = []
        for refs_id, matches in grouped.items():
            ref_row = select_reference(library_conn, refs_id)
            summary = reference_summary(ref_row, matched_fields=["pdf_full_text"])
            summary["attachment_count"] = attachment_count(library_conn, refs_id)
            summary["pdf_matches"] = matches[:3]
            results.append(summary)

    return {
        "library_id": library.id,
        "query": query,
        "pdf_index": str(library.pdf_index_path),
        "count": len(results),
        "results": results,
    }


def extract_pdf_text(
    reference_id: int,
    attachment_index: int | None = 0,
    library_id: str | None = None,
    max_chars: int | None = 4000,
) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise EndNoteError("pypdf is required for extract_pdf_text.") from exc

    attachments_payload = list_attachments(reference_id, library_id)
    attachments = attachments_payload["attachments"]
    if not attachments:
        raise EndNoteError(f"Reference {reference_id} has no attachments.")

    index = int(attachment_index or 0)
    if index < 0 or index >= len(attachments):
        raise EndNoteError(
            f"attachment_index {index} is out of range for {len(attachments)} attachments."
        )

    attachment = attachments[index]
    pdf_path = Path(attachment["resolved_path"])
    if not pdf_path.exists():
        raise EndNoteError(f"Attachment file does not exist: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise EndNoteError(f"Attachment is not a PDF: {pdf_path}")

    reader = PdfReader(str(pdf_path))
    chunks: list[str] = []
    char_budget = max(1, int(max_chars or 4000))
    for page in reader.pages:
        text = page.extract_text() or ""
        if text:
            chunks.append(text)
        if sum(len(chunk) for chunk in chunks) >= char_budget:
            break
    text = "\n".join(chunks)

    return {
        "library_id": attachments_payload["library_id"],
        "reference_id": int(reference_id),
        "attachment": attachment,
        "page_count": len(reader.pages),
        "max_chars": char_budget,
        "text": truncate(text, char_budget),
    }
