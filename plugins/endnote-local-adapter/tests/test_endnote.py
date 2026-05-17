from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from pypdf import PdfWriter

from endnote_mcp import endnote


INTEGER_FIELDS = {
    "id",
    "trash_state",
    "reference_type",
    "added_to_library",
    "record_last_updated",
}


def create_refs_schema(conn: sqlite3.Connection) -> None:
    columns = []
    for field in endnote.REFERENCE_FIELDS:
        if field == "id":
            columns.append("id INTEGER PRIMARY KEY")
        elif field in INTEGER_FIELDS:
            columns.append(f"{field} INTEGER NOT NULL DEFAULT 0")
        else:
            columns.append(f'{field} TEXT NOT NULL DEFAULT ""')
    conn.execute(f"CREATE TABLE refs ({', '.join(columns)})")
    conn.execute(
        """
        CREATE TABLE file_res (
          refs_id INTEGER NOT NULL,
          file_path TEXT NOT NULL DEFAULT "",
          file_type INTEGER NOT NULL,
          file_pos INTEGER NOT NULL
        )
        """
    )


def insert_reference(conn: sqlite3.Connection, **overrides: object) -> None:
    payload: dict[str, object] = {}
    for field in endnote.REFERENCE_FIELDS:
        if field == "id":
            payload[field] = 1
        elif field in INTEGER_FIELDS:
            payload[field] = 0
        else:
            payload[field] = ""
    payload.update(overrides)
    columns = list(payload)
    placeholders = ", ".join(["?"] * len(columns))
    conn.execute(
        f"INSERT INTO refs ({', '.join(columns)}) VALUES ({placeholders})",
        [payload[column] for column in columns],
    )


@pytest.fixture()
def configured_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    library_path = tmp_path / "Fixture Library.enl"
    data_dir = tmp_path / "Fixture Library.Data"
    pdf_dir = data_dir / "PDF" / "1234567890"
    sdb_dir = data_dir / "sdb"
    pdf_dir.mkdir(parents=True)
    sdb_dir.mkdir(parents=True)

    with sqlite3.connect(library_path) as conn:
        create_refs_schema(conn)
        insert_reference(
            conn,
            id=42,
            title="Magnetic order in layered materials",
            author="Ada Lovelace",
            year="2024",
            secondary_title="Journal of Useful Fixtures",
            abstract="A fixture paper about chiral charge density waves.",
            keywords="magnetic; layered; fixture",
            electronic_resource_number="10.1234/fixture",
            record_last_updated=99,
        )
        insert_reference(
            conn,
            id=43,
            title="Deleted reference",
            trash_state=1,
            author="Grace Hopper",
        )
        conn.execute(
            "INSERT INTO file_res (refs_id, file_path, file_type, file_pos) VALUES (?, ?, ?, ?)",
            (42, "1234567890/fixture.pdf", 1, 0),
        )

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with (pdf_dir / "fixture.pdf").open("wb") as handle:
        writer.write(handle)

    with sqlite3.connect(sdb_dir / "pdb.eni") as conn:
        conn.execute(
            """
            CREATE TABLE pdf_index (
              pdfi_id INTEGER PRIMARY KEY AUTOINCREMENT,
              version INTEGER UNSIGNED NOT NULL DEFAULT 0,
              refs_id INTEGER UNSIGNED NOT NULL DEFAULT 0,
              file_timestamp INTEGER UNSIGNED NOT NULL DEFAULT 0,
              subkey BLOB NOT NULL,
              contents TEXT NOT NULL DEFAULT "",
              tag TEXT NOT NULL DEFAULT ""
            )
            """
        )
        conn.execute(
            """
            INSERT INTO pdf_index (refs_id, subkey, contents)
            VALUES (?, ?, ?)
            """,
            (
                42,
                "1234567890/fixture.pdf",
                "The supplemental PDF discusses Berry curvature and magnetic domains.",
            ),
        )

    config_path = tmp_path / "libraries.local.json"
    config_path.write_text(
        json.dumps(
            {
                "defaultLibraryId": "fixture",
                "libraries": [
                    {
                        "id": "fixture",
                        "name": "Fixture Library",
                        "path": str(library_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(endnote, "LOCAL_CONFIG_PATH", config_path)
    monkeypatch.setattr(endnote, "EXAMPLE_CONFIG_PATH", tmp_path / "missing.example.json")
    return library_path


def test_list_libraries_reports_configured_default(configured_library: Path) -> None:
    payload = endnote.list_libraries()

    assert payload["default_library_id"] == "fixture"
    assert payload["libraries"][0]["id"] == "fixture"
    assert payload["libraries"][0]["default"] is True
    assert payload["libraries"][0]["exists"] is True


def test_search_references_finds_metadata_and_counts_attachments(
    configured_library: Path,
) -> None:
    payload = endnote.search_references("magnetic")

    assert payload["count"] == 1
    result = payload["results"][0]
    assert result["id"] == 42
    assert result["attachment_count"] == 1
    assert "title" in result["matched_fields"] or "keywords" in result["matched_fields"]


def test_get_reference_and_list_attachments_resolve_pdf_path(
    configured_library: Path,
) -> None:
    detail = endnote.get_reference(42)
    attachments = endnote.list_attachments(42)

    assert detail["reference"]["title"] == "Magnetic order in layered materials"
    assert attachments["count"] == 1
    attachment = attachments["attachments"][0]
    detail_attachment = detail["attachments"][0]
    resolved_path = Path(attachment["resolved_path"])
    parent_path = resolved_path.parent

    assert attachment["exists"] is True
    assert attachment["resolved_path"].endswith("1234567890/fixture.pdf")
    assert attachment["file_uri"] == resolved_path.as_uri()
    assert attachment["file_uri"].startswith("file://")
    assert "Fixture%20Library.Data" in attachment["file_uri"]
    assert attachment["parent_path"] == str(parent_path)
    assert attachment["parent_uri"] == parent_path.as_uri()
    assert attachment["parent_uri"].startswith("file://")
    assert attachment["markdown_link"] == f"[Open PDF](<{resolved_path}>)"
    assert attachment["markdown_parent_link"] == f"[Show containing folder](<{parent_path}>)"
    assert detail_attachment["markdown_link"] == attachment["markdown_link"]


def test_attachment_links_make_relative_missing_paths_clickable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    relative_path = Path("Missing Folder") / "missing paper.pdf"
    absolute_path = relative_path.absolute()

    links = endnote.attachment_links(relative_path)

    assert absolute_path.exists() is False
    assert links["file_uri"] == absolute_path.as_uri()
    assert links["parent_path"] == str(absolute_path.parent)
    assert links["parent_uri"] == absolute_path.parent.as_uri()
    assert links["markdown_link"] == f"[Open PDF](<{absolute_path}>)"
    assert links["markdown_parent_link"] == f"[Show containing folder](<{absolute_path.parent}>)"


def test_search_pdf_full_text_uses_endnote_pdf_index(configured_library: Path) -> None:
    payload = endnote.search_pdf_full_text("Berry curvature", context_chars=50)

    assert payload["count"] == 1
    result = payload["results"][0]
    assert result["id"] == 42
    assert result["matched_fields"] == ["pdf_full_text"]
    assert "Berry curvature" in result["pdf_matches"][0]["snippet"]


def test_extract_pdf_text_reads_attachment_on_request(configured_library: Path) -> None:
    payload = endnote.extract_pdf_text(42, max_chars=100)

    assert payload["reference_id"] == 42
    assert payload["page_count"] == 1
    assert payload["attachment"]["exists"] is True


def test_missing_library_path_fails_for_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "libraries.local.json"
    config_path.write_text(
        json.dumps(
            {
                "defaultLibraryId": "missing",
                "libraries": [
                    {
                        "id": "missing",
                        "name": "Missing",
                        "path": str(tmp_path / "missing.enl"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(endnote, "LOCAL_CONFIG_PATH", config_path)

    with pytest.raises(endnote.EndNoteError, match="does not exist"):
        endnote.search_references("anything")


def test_env_config_path_overrides_local_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_config_path = tmp_path / "env-config.json"
    env_config_path.write_text(
        json.dumps(
            {
                "defaultLibraryId": "env-library",
                "libraries": [
                    {
                        "id": "env-library",
                        "name": "Env Library",
                        "path": str(tmp_path / "Env Library.enl"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(endnote.CONFIG_ENV_VAR, str(env_config_path))

    payload = endnote.list_libraries()

    assert payload["config_path"] == str(env_config_path)
    assert payload["default_library_id"] == "env-library"


def test_env_config_path_must_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(endnote.CONFIG_ENV_VAR, str(tmp_path / "missing.json"))

    with pytest.raises(endnote.EndNoteError, match=endnote.CONFIG_ENV_VAR):
        endnote.list_libraries()


def test_open_sqlite_readonly_rejects_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "readonly.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE refs (id INTEGER PRIMARY KEY)")

    with endnote.open_sqlite_readonly(db_path) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE should_fail (id INTEGER)")
