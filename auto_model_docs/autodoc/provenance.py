"""MVP provenance stamping for generated .docx documents.

Records 5 required fields in two places for every generated doc:

1. Word document custom properties (visible in File > Info > Properties)
2. A SQLite row in ``autodoc_provenance.db`` (co-located with ``autodoc_jobs.db``)

The SQLite write and the .docx commit are performed atomically with respect
to each other: if the .docx commit fails after the SQLite insert, the row is
rolled back.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from lxml import etree

logger = logging.getLogger(__name__)

MVP_FIELDS = (
    "bundle_id",
    "policy_version_id",
    "commit_sha",
    "generated_by_user",
    "generated_at",
)

_CUSTOM_PROPS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
_VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CUSTOM_PROPS_CT = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
_CUSTOM_PROPS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
_FMT_ID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"


@dataclass(frozen=True)
class ProvenanceRecord:
    provenance_id: str
    bundle_id: Optional[str]
    policy_version_id: Optional[str]
    commit_sha: Optional[str]
    generated_by_user: str
    generated_at: str

    def mvp_properties(self) -> dict[str, str]:
        return {f: ("" if getattr(self, f) is None else str(getattr(self, f))) for f in MVP_FIELDS}


def capture_context(
    bundle_id: Optional[str] = None,
    policy_version_id: Optional[str] = None,
) -> ProvenanceRecord:
    """Capture the 5 MVP provenance fields at Job start time."""
    return ProvenanceRecord(
        provenance_id=uuid.uuid4().hex[:12],
        bundle_id=bundle_id,
        policy_version_id=policy_version_id,
        commit_sha=_resolve_commit_sha(),
        generated_by_user=os.environ.get("DOMINO_STARTING_USERNAME") or "unknown",
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _resolve_commit_sha() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        sha = result.stdout.strip()
        if sha:
            return sha
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
        logger.debug("git rev-parse failed: %s", exc)
    return os.environ.get("DOMINO_WORKING_DIR_GIT_REF_ID") or None


# ---------------------------------------------------------------------------
# Word custom-properties stamping
# ---------------------------------------------------------------------------

def stamp_word_properties(docx_path: str, record: ProvenanceRecord) -> None:
    """Inject the 5 MVP fields as Word custom properties into a saved .docx."""
    properties = record.mvp_properties()
    custom_xml = _build_custom_xml(properties)

    with zipfile.ZipFile(docx_path, "r") as zin:
        entries = {name: zin.read(name) for name in zin.namelist()}

    entries["[Content_Types].xml"] = _ensure_content_type(entries["[Content_Types].xml"])
    entries["_rels/.rels"] = _ensure_relationship(entries["_rels/.rels"])
    entries["docProps/custom.xml"] = custom_xml

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".docx")
    os.close(tmp_fd)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for name, data in entries.items():
                zout.writestr(name, data)
        os.replace(tmp_path, docx_path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _build_custom_xml(properties: dict[str, str]) -> bytes:
    nsmap = {None: _CUSTOM_PROPS_NS, "vt": _VT_NS}
    root = etree.Element(f"{{{_CUSTOM_PROPS_NS}}}Properties", nsmap=nsmap)
    pid = 2
    for name, value in properties.items():
        prop = etree.SubElement(root, f"{{{_CUSTOM_PROPS_NS}}}property")
        prop.set("fmtid", _FMT_ID)
        prop.set("pid", str(pid))
        prop.set("name", name)
        val = etree.SubElement(prop, f"{{{_VT_NS}}}lpwstr")
        val.text = "" if value is None else str(value)
        pid += 1
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _ensure_content_type(ct_xml: bytes) -> bytes:
    tree = etree.fromstring(ct_xml)
    ns = {"ct": _CT_NS}
    if tree.find("ct:Override[@PartName='/docProps/custom.xml']", namespaces=ns) is None:
        override = etree.SubElement(tree, f"{{{_CT_NS}}}Override")
        override.set("PartName", "/docProps/custom.xml")
        override.set("ContentType", _CUSTOM_PROPS_CT)
    return etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)


def _ensure_relationship(rels_xml: bytes) -> bytes:
    tree = etree.fromstring(rels_xml)
    ns = {"r": _RELS_NS}
    if tree.find("r:Relationship[@Target='docProps/custom.xml']", namespaces=ns) is None:
        rid_nums = [
            int(r.get("Id", "rId0").replace("rId", ""))
            for r in tree.findall("r:Relationship", ns)
            if r.get("Id", "").startswith("rId")
        ]
        next_id = max(rid_nums, default=0) + 1
        rel = etree.SubElement(tree, f"{{{_RELS_NS}}}Relationship")
        rel.set("Id", f"rId{next_id}")
        rel.set("Type", _CUSTOM_PROPS_REL)
        rel.set("Target", "docProps/custom.xml")
    return etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)


def read_custom_properties(docx_path: str) -> dict[str, Optional[str]]:
    """Read custom properties from a saved .docx. Intended for tests/inspection."""
    with zipfile.ZipFile(docx_path, "r") as z:
        if "docProps/custom.xml" not in z.namelist():
            return {}
        xml = z.read("docProps/custom.xml")
    tree = etree.fromstring(xml)
    props: dict[str, Optional[str]] = {}
    for prop in tree.findall(f"{{{_CUSTOM_PROPS_NS}}}property"):
        name = prop.get("name")
        val_el = prop.find(f"{{{_VT_NS}}}lpwstr")
        props[name] = val_el.text if val_el is not None else None
    return props


# ---------------------------------------------------------------------------
# SQLite index
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS provenance (
    provenance_id TEXT PRIMARY KEY,
    bundle_id TEXT,
    policy_version_id TEXT,
    commit_sha TEXT,
    generated_by_user TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    docx_path TEXT NOT NULL,
    created_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_provenance_bundle ON provenance(bundle_id);
"""


def default_db_path() -> str:
    return os.environ.get("AUTODOC_PROVENANCE_DB") or str(Path.cwd() / "autodoc_provenance.db")


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    return conn


def write_provenance_row(db_path: str, record: ProvenanceRecord, docx_path: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO provenance
                (provenance_id, bundle_id, policy_version_id, commit_sha,
                 generated_by_user, generated_at, docx_path, created_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.provenance_id,
                record.bundle_id,
                record.policy_version_id,
                record.commit_sha,
                record.generated_by_user,
                record.generated_at,
                docx_path,
                time.time(),
            ),
        )
        conn.commit()


def delete_provenance_row(db_path: str, provenance_id: str) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM provenance WHERE provenance_id = ?", (provenance_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# Atomic save
# ---------------------------------------------------------------------------

def save_with_provenance(
    doc,
    final_path: str,
    record: ProvenanceRecord,
    db_path: str,
    commit_fn: Callable[[str, str], None],
) -> None:
    """Stamp provenance into ``doc``, write atomically to ``final_path``.

    Sequence:
      1. Save ``doc`` to a local temp .docx.
      2. Inject custom properties into the temp file.
      3. Insert the SQLite provenance row.
      4. Call ``commit_fn(temp_path, final_path)`` to publish the .docx
         (e.g. os.replace or a DatasetStore upload).
      5. If the commit raises, delete the SQLite row and re-raise.
    """
    tmp_fd, temp_path = tempfile.mkstemp(suffix=".docx")
    os.close(tmp_fd)
    try:
        doc.save(temp_path)
        stamp_word_properties(temp_path, record)
        write_provenance_row(db_path, record, final_path)
        try:
            commit_fn(temp_path, final_path)
        except Exception:
            delete_provenance_row(db_path, record.provenance_id)
            raise
    finally:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
