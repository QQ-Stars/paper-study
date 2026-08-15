from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import json
import re


_PAPER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,179}\Z")
_WINDOWS_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.IGNORECASE)


class ObsidianProjectionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ExportOptions:
    export_source: bool = True
    export_explainer: bool = True
    export_translation: bool = True


@dataclass(frozen=True, slots=True)
class ProjectionArtifact:
    artifact_id: str
    markdown: str
    source_hash: str


@dataclass(frozen=True, slots=True)
class ProjectionSnapshot:
    paper_id: str
    title: str
    title_zh: str | None
    authors: str | None
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    paper_source_hash: str
    source_markdown: str | None
    source_hash: str | None
    explainer: ProjectionArtifact | None
    translation: ProjectionArtifact | None
    note_markdown: str | None
    note_source_hash: str | None
    pdf_link: str | None = None
    pdf_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectionFile:
    path: str
    kind: str
    data: bytes
    source_hash: str
    artifact_id: str | None = None
    ownership: str = "managed"


@dataclass(frozen=True, slots=True)
class ProjectionPlan:
    paper_id: str
    files: tuple[ProjectionFile, ...]


@dataclass(frozen=True, slots=True)
class ProjectionPaths:
    paper: str
    source: str
    explainer: str
    translation: str
    note: str
    pdf: str


def project_paths(paper_id: str) -> ProjectionPaths:
    validate_paper_file_id(paper_id)
    return ProjectionPaths(
        paper=f"Papers/{paper_id}.md",
        source=f"Sources/{paper_id}.md",
        explainer=f"Explainers/{paper_id}.md",
        translation=f"Translations/{paper_id}.md",
        note=f"Notes/{paper_id}.md",
        pdf=f"Attachments/PDF/{paper_id}.pdf",
    )


def validate_paper_file_id(paper_id: str) -> str:
    if (
        not isinstance(paper_id, str)
        or not _PAPER_ID.fullmatch(paper_id)
        or paper_id in {".", ".."}
        or paper_id.endswith(".")
        or _WINDOWS_DEVICE.fullmatch(paper_id)
    ):
        raise ObsidianProjectionError(
            "OBSIDIAN_PAPER_ID_UNSAFE",
            "Paper id cannot be represented by the fixed Obsidian layout.",
        )
    return paper_id


def build_projection_plans(
    snapshots: Sequence[ProjectionSnapshot],
    options: ExportOptions,
    *,
    existing_paths: Iterable[str] = (),
) -> tuple[ProjectionPlan, ...]:
    seen_snapshot_ids: dict[str, str] = {}
    for snapshot in snapshots:
        paper_id = validate_paper_file_id(snapshot.paper_id)
        folded = paper_id.casefold()
        if folded in seen_snapshot_ids:
            raise ObsidianProjectionError(
                "OBSIDIAN_PAPER_ID_CASE_COLLISION",
                "Paper ids collide under case-insensitive Vault path rules.",
            )
        seen_snapshot_ids[folded] = paper_id

    existing_ids: dict[str, str] = {}
    for path in existing_paths:
        existing_id = _paper_id_from_projection_path(path)
        if existing_id is not None:
            existing_ids.setdefault(existing_id.casefold(), existing_id)
    for folded, paper_id in seen_snapshot_ids.items():
        existing_id = existing_ids.get(folded)
        if existing_id is not None and existing_id != paper_id:
            raise ObsidianProjectionError(
                "OBSIDIAN_PAPER_ID_CASE_COLLISION",
                "Paper id collides with an existing Vault target.",
            )

    return tuple(build_projection_plan(snapshot, options) for snapshot in snapshots)


def _paper_id_from_projection_path(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    directory, separator, name = normalized.rpartition("/")
    if not separator:
        return None
    suffixes = {
        "Papers": ".md",
        "Sources": ".md",
        "Explainers": ".md",
        "Translations": ".md",
        "Notes": ".md",
        "Attachments/PDF": ".pdf",
    }
    for expected_directory, suffix in suffixes.items():
        if directory == expected_directory and name.endswith(suffix):
            return name[: -len(suffix)]
    return None


def build_projection_plan(
    snapshot: ProjectionSnapshot,
    options: ExportOptions,
) -> ProjectionPlan:
    paths = project_paths(snapshot.paper_id)
    source_available = options.export_source and snapshot.source_markdown is not None
    explainer_available = options.export_explainer and snapshot.explainer is not None
    translation_available = options.export_translation and snapshot.translation is not None

    files = [
        ProjectionFile(
            path=paths.paper,
            kind="paper",
            data=_render_paper(
                snapshot,
                source_available=source_available,
                explainer_available=explainer_available,
                translation_available=translation_available,
            ),
            source_hash=snapshot.paper_source_hash,
        )
    ]
    if source_available:
        assert snapshot.source_markdown is not None
        assert snapshot.source_hash is not None
        files.append(
            ProjectionFile(
                path=paths.source,
                kind="source",
                data=_render_managed_content(
                    snapshot,
                    kind="source",
                    source_hash=snapshot.source_hash,
                    markdown=snapshot.source_markdown,
                ),
                source_hash=snapshot.source_hash,
            )
        )
    if explainer_available:
        assert snapshot.explainer is not None
        files.append(
            ProjectionFile(
                path=paths.explainer,
                kind="explainer",
                data=_render_managed_content(
                    snapshot,
                    kind="explainer",
                    source_hash=snapshot.explainer.source_hash,
                    artifact=snapshot.explainer,
                    markdown=snapshot.explainer.markdown,
                ),
                source_hash=snapshot.explainer.source_hash,
                artifact_id=snapshot.explainer.artifact_id,
            )
        )
    if translation_available:
        assert snapshot.translation is not None
        files.append(
            ProjectionFile(
                path=paths.translation,
                kind="translation",
                data=_render_managed_content(
                    snapshot,
                    kind="translation",
                    source_hash=snapshot.translation.source_hash,
                    artifact=snapshot.translation,
                    markdown=snapshot.translation.markdown,
                ),
                source_hash=snapshot.translation.source_hash,
                artifact_id=snapshot.translation.artifact_id,
            )
        )
    if snapshot.note_markdown:
        if snapshot.note_source_hash is None:
            raise ValueError("note_source_hash is required for a note seed")
        files.append(
            ProjectionFile(
                path=paths.note,
                kind="note",
                data=_render_note_seed(snapshot),
                source_hash=snapshot.note_source_hash,
                ownership="user",
            )
        )
    return ProjectionPlan(paper_id=snapshot.paper_id, files=tuple(files))


def _yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _frontmatter(
    fields: list[tuple[str, str | bool]],
    lists: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> str:
    lines = ["---"]
    for key, value in fields:
        encoded = "true" if value is True else "false" if value is False else _yaml_scalar(value)
        lines.append(f"{key}: {encoded}")
    for key, values in lists:
        if values:
            lines.append(f"{key}:")
            lines.extend(f"  - {_yaml_scalar(value)}" for value in values)
        else:
            lines.append(f"{key}: []")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _canonical_body(markdown: str) -> str:
    return markdown.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"


def _render_paper(
    snapshot: ProjectionSnapshot,
    *,
    source_available: bool,
    explainer_available: bool,
    translation_available: bool,
) -> bytes:
    fields: list[tuple[str, str | bool]] = [
        ("paper-study-managed", True),
        ("paper-id", snapshot.paper_id),
        ("kind", "paper"),
        ("source-hash", snapshot.paper_source_hash),
        ("title", snapshot.title),
    ]
    if snapshot.title_zh is not None:
        fields.append(("titleZh", snapshot.title_zh))
    if snapshot.authors is not None:
        fields.append(("authors", snapshot.authors))

    body = [f"# {snapshot.title}", ""]
    if snapshot.title_zh is not None:
        body.extend((f"- **Title (ZH):** {snapshot.title_zh}",))
    if snapshot.authors is not None:
        body.extend((f"- **Authors:** {snapshot.authors}",))
    body.extend(
        (
            "",
            "## Source",
            "",
            f"[Open source](../Sources/{snapshot.paper_id}.md)" if source_available else "*Source unavailable.*",
            "",
            "## Explainer",
            "",
            f"[Open explainer](../Explainers/{snapshot.paper_id}.md)"
            if explainer_available
            else "*Explainer unavailable.*",
            "",
            "## Translation",
            "",
            f"[Open translation](../Translations/{snapshot.paper_id}.md)"
            if translation_available
            else "*Translation unavailable.*",
            "",
            "## Notes",
            "",
            f"[Open notes](../Notes/{snapshot.paper_id}.md)",
        )
    )
    if snapshot.pdf_link is not None:
        body.extend(("", "## PDF", "", f"[Open PDF]({snapshot.pdf_link})"))
    data = _frontmatter(
        fields,
        (
            ("aliases", snapshot.aliases),
            ("tags", tuple(sorted(set(snapshot.tags)))),
        ),
    ) + "\n".join(body)
    return _canonical_body(data).encode("utf-8")


def _render_managed_content(
    snapshot: ProjectionSnapshot,
    *,
    kind: str,
    source_hash: str,
    markdown: str,
    artifact: ProjectionArtifact | None = None,
) -> bytes:
    fields: list[tuple[str, str | bool]] = [
        ("paper-study-managed", True),
        ("paper-id", snapshot.paper_id),
        ("kind", kind),
        ("source-hash", source_hash),
    ]
    if artifact is not None:
        fields.append(("artifact-id", artifact.artifact_id))
    fields.append(("title", snapshot.title))
    return (_frontmatter(fields) + _canonical_body(markdown)).encode("utf-8")


def _render_note_seed(snapshot: ProjectionSnapshot) -> bytes:
    assert snapshot.note_source_hash is not None
    assert snapshot.note_markdown is not None
    return (
        _frontmatter(
            [
                ("paper-study-note-seed", True),
                ("paper-id", snapshot.paper_id),
                ("kind", "note"),
                ("source-hash", snapshot.note_source_hash),
            ]
        )
        + _canonical_body(snapshot.note_markdown)
    ).encode("utf-8")
