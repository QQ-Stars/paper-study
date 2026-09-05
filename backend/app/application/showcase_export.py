from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import unicodedata
from urllib.parse import urlparse
from uuid import uuid4
from collections.abc import Callable, Mapping, Sequence

from backend.app.domain import PublicationValidationError, validate_sha256


PUBLICATION_CONCLUSIONS = (
    "reproduced",
    "partial",
    "inconsistent",
    "not_reproduced",
)

CONCLUSION_LABELS = {
    "reproduced": "完全复现",
    "partial": "部分复现",
    "inconsistent": "结果存在偏差",
    "not_reproduced": "未能复现",
}

PROJECT_KINDS = ("reproduction", "article")

_SLUG = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff-]{0,79}$", re.UNICODE)
_PROJECT_ID = re.compile(r"repro_[a-f0-9]{32}\Z")
_ARTIFACT_URL = re.compile(
    r"(?P<url>/api/v2/reproductions/(?P<project>repro_[a-f0-9]{32})/artifacts/"
    r"(?P<artifact>[A-Za-z0-9_-]{1,100})/download)"
)
_UNSAFE_MARKDOWN = re.compile(
    r"(?is)(<\s*(?:script|iframe|object|embed|style|link|meta|form|input|button)\b|"
    r"\bon[a-z0-9_-]+\s*=|javascript\s*:|vbscript\s*:|data\s*:\s*text/html|"
    r"file\s*://|\x00)"
)
_PRIVATE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|mnt|tmp)/)")
_SECRET = re.compile(
    r"(?i)(api[_ -]?key|access[_ -]?token|secret|password|authorization)"
    r"\s*[:=]\s*[^\s,;]+"
)


@dataclass(frozen=True, slots=True)
class PublicationValidation:
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "valid": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class ShowcaseExportResult:
    slug: str
    url: str
    content_hash: str
    files: tuple[str, ...]
    exported_at: str


def normalize_slug(value: object, *, fallback: str) -> str:
    raw = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    raw = re.sub(r"[^\w\u4e00-\u9fff]+", "-", raw, flags=re.UNICODE)
    raw = re.sub(r"-{2,}", "-", raw).strip("-")[:80]
    if not raw:
        raw = fallback
    if not _SLUG.fullmatch(raw):
        raw = re.sub(r"[^\w\u4e00-\u9fff-]", "-", fallback, flags=re.UNICODE).strip("-")
    return raw[:80] or "reproduction"


def _project_kind(project: Mapping[str, object]) -> str:
    value = str(project.get("projectKind") or "reproduction")
    return value if value in PROJECT_KINDS else "reproduction"


def _content_namespace(project: Mapping[str, object]) -> str:
    return "articles" if _project_kind(project) == "article" else "reproductions"


def _public_asset_relative(
    *,
    namespace: str,
    slug: str,
    artifact: Mapping[str, object],
) -> PurePosixPath:
    """Keep exported assets in a stable, pre-existing watched directory.

    Hexo's watcher handles a new directory by recursively scanning it while it
    also receives the child file's create event.  On Windows those two events
    can race and insert the same Asset twice.  A flat, namespace-scoped filename
    avoids creating a directory for every publication while retaining stable,
    project-specific URLs.
    """

    artifact_id = str(artifact.get("id") or "")
    suffix = Path(str(artifact.get("filename") or "")).suffix.lower()
    return PurePosixPath(
        "source",
        "images",
        namespace,
        f"{slug}--{artifact_id}{suffix}",
    )


def _public_asset_url(
    *,
    namespace: str,
    slug: str,
    artifact: Mapping[str, object],
) -> str:
    relative = _public_asset_relative(
        namespace=namespace,
        slug=slug,
        artifact=artifact,
    )
    return "/" + PurePosixPath(*relative.parts[1:]).as_posix()


def validate_publication_snapshot(
    project: Mapping[str, object],
    paper: Mapping[str, object] | None,
    publication: Mapping[str, object],
    *,
    artifact_resolver: Callable[[str, str | None], Path] | None = None,
) -> PublicationValidation:
    errors: list[str] = []
    warnings: list[str] = []
    project_id = str(project.get("id") or "")
    raw_kind = str(project.get("projectKind") or "reproduction")
    project_kind = _project_kind(project)
    if raw_kind not in PROJECT_KINDS:
        errors.append("项目类型无效")
    if not _PROJECT_ID.fullmatch(project_id):
        errors.append("项目 ID 无效")
    if str(project.get("status") or "") != "completed":
        errors.append(
            "复现流程必须处于“复现已完成”状态"
            if project_kind == "reproduction"
            else "文章必须处于“已完成”状态"
        )
    if str(publication.get("decision") or "") != "approved":
        errors.append("需要人工批准公开")
    conclusion = str(publication.get("aggregateConclusion") or "")
    if project_kind == "reproduction" and conclusion not in PUBLICATION_CONCLUSIONS:
        errors.append("必须选择复现结论")
    title = _text(publication.get("publicTitle"))
    summary = _text(publication.get("publicSummary"))
    if not title:
        errors.append("公开标题不能为空")
    if not summary:
        errors.append("公开摘要不能为空")
    if len(summary) > 20_000:
        errors.append("公开摘要过长")

    slug = str(publication.get("stableSlug") or "")
    if not _SLUG.fullmatch(slug):
        errors.append("稳定 slug 只能包含字母、数字、中文、下划线和连字符")

    document = str((project.get("document") or {}).get("content") or "")
    if not document.strip():
        errors.append("复现正文不能为空" if project_kind == "reproduction" else "文章正文不能为空")
    if len(document) > 2_000_000:
        errors.append("正文超过 2 MB 限制")
    if _UNSAFE_MARKDOWN.search(document):
        errors.append("正文包含不允许公开的 HTML 或脚本内容")
    if _PRIVATE_PATH.search(document):
        errors.append("正文包含本地绝对路径")
    if _SECRET.search(document):
        errors.append("正文包含疑似密钥或访问令牌")
    for value, label in ((title, "公开标题"), (summary, "公开摘要")):
        if _UNSAFE_MARKDOWN.search(value) or _PRIVATE_PATH.search(value) or _SECRET.search(value):
            errors.append(f"{label}包含不允许公开的内容")

    if project_kind == "reproduction" and paper is None:
        errors.append("关联论文不存在")
    for field, label in (("paperUrl", "论文链接"), ("codeUrl", "代码链接")):
        value = publication.get(field)
        if value and not _safe_external_url(str(value)):
            errors.append(f"{label}必须是 http 或 https 链接")
    dataset_urls = _string_list(publication.get("datasetUrls"))
    if len(dataset_urls) > 30:
        errors.append("数据集链接不能超过 30 个")
    for value in dataset_urls:
        if not _safe_external_url(value):
            errors.append("数据集链接必须是 http 或 https 链接")

    artifacts = _mappings(project.get("artifacts"))
    artifact_by_id = {str(item.get("id")): item for item in artifacts}
    public_ids = _string_list(publication.get("publicArtifactIds"))
    missing = [value for value in public_ids if value not in artifact_by_id]
    if missing:
        errors.append("公开附件列表包含不存在的附件")
    for artifact_id in public_ids:
        artifact = artifact_by_id.get(artifact_id)
        if artifact is None:
            continue
        mime = str(artifact.get("mimeType") or "").lower()
        if mime in {"text/html", "application/xhtml+xml"}:
            errors.append("HTML 附件不能直接公开")
        if artifact_resolver is not None:
            storage = str(artifact.get("storageKey") or "")
            try:
                path = artifact_resolver(storage, project_id)
            except Exception:
                path = Path()
            if not path.is_file() or path.is_symlink():
                errors.append(f"公开附件不存在：{artifact.get('filename') or artifact_id}")
            else:
                expected_size = _int_or_none(artifact.get("sizeBytes"))
                expected_sha = str(artifact.get("sha256") or "")
                if expected_size is None or not _valid_digest(expected_sha):
                    errors.append(f"公开附件校验信息无效：{artifact.get('filename') or artifact_id}")
                else:
                    size, digest = _file_digest(path)
                    if size != expected_size or digest != expected_sha:
                        errors.append(f"公开附件校验失败：{artifact.get('filename') or artifact_id}")

    for match in _ARTIFACT_URL.finditer(document):
        if match.group("project") != project_id:
            errors.append("正文引用了其他复现项目的附件")
        elif match.group("artifact") not in public_ids:
            errors.append("正文引用了未标记为公开的附件")

    if project_kind == "reproduction":
        results = _mappings(project.get("results"))
        runs = _mappings(project.get("runs"))
        if not results and not any(str(run.get("resultSummary") or "").strip() for run in runs):
            warnings.append("尚未记录结构化指标，详情页将主要展示 Markdown 结论")
        if any(str(run.get("status") or "") == "failed" for run in runs):
            warnings.append("项目包含失败运行；失败记录会以背景信息展示")
    return PublicationValidation(not errors, tuple(errors), tuple(warnings))


def rewrite_public_markdown(
    markdown: str,
    *,
    project_id: str,
    slug: str,
    namespace: str = "reproductions",
    artifacts: Sequence[Mapping[str, object]],
    public_artifact_ids: Sequence[str],
) -> str:
    by_id = {str(item.get("id")): item for item in artifacts}
    public_set = set(public_artifact_ids)

    def replace(match: re.Match[str]) -> str:
        artifact_id = match.group("artifact")
        artifact = by_id.get(artifact_id)
        if match.group("project") != project_id or artifact is None or artifact_id not in public_set:
            raise PublicationValidationError()
        return _public_asset_url(
            namespace=namespace,
            slug=slug,
            artifact=artifact,
        )

    rewritten = _ARTIFACT_URL.sub(replace, markdown)
    if _UNSAFE_MARKDOWN.search(rewritten) or _PRIVATE_PATH.search(rewritten):
        raise PublicationValidationError()
    return _canonical_markdown(rewritten)


def render_front_matter(
    *,
    project: Mapping[str, object],
    paper: Mapping[str, object] | None,
    publication: Mapping[str, object],
    slug: str,
    cover: str | None,
) -> str:
    project_kind = _project_kind(project)
    paper = paper or {}
    namespace = _content_namespace(project)
    tags = _unique_strings(
        [
            *_string_list(project.get("tags")),
            *(
                [str(publication.get("aggregateConclusion") or "")]
                if project_kind == "reproduction"
                else []
            ),
        ]
    )
    # Fluid's built-in category generator provides the public split between
    # reproductions and articles.  Keep those category names stable so the
    # standard category pages can be linked from the theme navigation.
    categories = _unique_strings(
        [
            "论文复现",
            str(paper.get("topic") or "研究方向"),
            str(paper.get("venue") or ""),
        ]
        if project_kind == "reproduction"
        else ["文章"]
    )
    authors = _as_authors(paper.get("authors")) if project_kind == "reproduction" else []
    lines = ["---"]
    fields: list[tuple[str, object]] = [
        ("title", str(publication.get("publicTitle") or project.get("name") or "未命名文章")),
        # Fluid uses `subtitle` as the visible post heading.  The publication
        # summary belongs in `description`, which the stock index template
        # uses as its excerpt without replacing the article title.
        ("description", str(publication.get("publicSummary") or "")),
        ("date", str(project.get("createdAt") or datetime.now(timezone.utc).isoformat())),
        ("updated", str(project.get("updatedAt") or "")),
        ("layout", "post"),
        ("permalink", f"{namespace}/{slug}/"),
        ("type", project_kind),
        ("content_type", project_kind),
        ("math", True),
        ("mermaid", True),
        ("project_id", str(project.get("id") or "")),
        ("code_url", str(publication.get("codeUrl") or "")),
        ("index_img", cover or ""),
    ]
    if project_kind == "reproduction":
        fields.extend(
            [
                ("paper_id", str(project.get("paperId") or "")),
                ("paper_year", str(paper.get("year") or "")),
                ("venue", str(paper.get("venue") or "")),
                ("reproduction_status", str(project.get("status") or "completed")),
                ("reproduction_conclusion", str(publication.get("aggregateConclusion") or "")),
                ("paper_url", str(publication.get("paperUrl") or paper.get("url") or paper.get("pdf_url") or "")),
            ]
        )
    elif paper:
        # Articles may optionally point back to a library paper. Keep that
        # relationship explicit in the static metadata so catalog pages can
        # display it without consulting the private database.
        fields.extend(
            [
                ("paper_id", str(project.get("paperId") or "")),
                ("paper_title", str(paper.get("title_zh") or paper.get("title") or project.get("paperTitle") or "")),
                ("paper_year", str(paper.get("year") or "")),
                ("venue", str(paper.get("venue") or "")),
            ]
        )
    article_source_url = (
        publication.get("paperUrl")
        or (paper or {}).get("url")
        or (paper or {}).get("pdf_url")
    )
    if project_kind == "article" and article_source_url:
        fields.append(("source_url", str(article_source_url)))
    for key, value in fields:
        encoded = "true" if value is True else "false" if value is False else _yaml_scalar(str(value))
        lines.append(f"{key}: {encoded}")
    lines.append("authors:")
    lines.extend(f"  - {_yaml_scalar(value)}" for value in authors)
    lines.append("categories:")
    lines.extend(f"  - {_yaml_scalar(value)}" for value in categories if value)
    lines.append("tags:")
    lines.extend(f"  - {_yaml_scalar(value)}" for value in tags if value)
    lines.append("dataset_urls:")
    lines.extend(f"  - {_yaml_scalar(value)}" for value in _string_list(publication.get("datasetUrls")))
    lines.append("---")
    return "\n".join(lines) + "\n"


def render_public_body(
    *,
    markdown: str,
    publication: Mapping[str, object],
    project: Mapping[str, object],
    paper: Mapping[str, object] | None,
) -> str:
    project_kind = _project_kind(project)
    summary = _canonical_markdown(str(publication.get("publicSummary") or "")).strip()
    if project_kind == "article":
        sections = [
            f"> {summary.replace(chr(10), chr(10) + '> ')}" if summary else "",
            _canonical_markdown(markdown).strip(),
        ]
        if paper is not None:
            sections.append("## 关联论文\n\n" + _render_paper_info(paper, publication))
        return "\n\n".join(section for section in sections if section.strip()).rstrip() + "\n"

    if paper is None:
        raise PublicationValidationError()
    conclusion = str(publication.get("aggregateConclusion") or "")
    label = CONCLUSION_LABELS.get(conclusion, conclusion)
    sections = [
        "## 论文信息\n\n" + _render_paper_info(paper, publication),
        "## 复现结论摘要\n\n"
        + f"> **复现结论：{label}**\n>\n> {summary.replace(chr(10), chr(10) + '> ')}\n",
        _canonical_markdown(markdown).strip(),
        "## 原论文与复现结果\n\n" + _render_results(_mappings(project.get("results"))),
        "## 实验环境与运行摘要\n\n" + _render_runs(_mappings(project.get("runs"))),
    ]
    return "\n\n".join(section for section in sections if section.strip()).rstrip() + "\n"


class ShowcaseExporter:
    """Write a private-database-free Hexo source tree with a managed manifest."""

    def __init__(
        self,
        root: Path,
        *,
        artifact_resolver: Callable[[str, str | None], Path],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = root.expanduser().resolve()
        self._artifact_resolver = artifact_resolver
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def export(
        self,
        *,
        project: Mapping[str, object],
        paper: Mapping[str, object] | None,
        publication: Mapping[str, object],
    ) -> ShowcaseExportResult:
        validation = validate_publication_snapshot(
            project,
            paper,
            publication,
            artifact_resolver=self._artifact_resolver,
        )
        if not validation.ok:
            raise PublicationValidationError()
        project_id = str(project["id"])
        slug = str(publication["stableSlug"])
        project_kind = _project_kind(project)
        namespace = _content_namespace(project)
        artifacts = _mappings(project.get("artifacts"))
        public_ids = _string_list(publication.get("publicArtifactIds"))
        cover_id = next(
            (
                item_id
                for item_id in public_ids
                if str(next((a.get("mimeType") for a in artifacts if str(a.get("id")) == item_id), "")).startswith("image/")
            ),
            None,
        )
        cover_artifact = next(
            (artifact for artifact in artifacts if str(artifact.get("id")) == cover_id),
            None,
        )
        cover = (
            _public_asset_url(
                namespace=namespace,
                slug=slug,
                artifact=cover_artifact,
            )
            if cover_artifact is not None
            else None
        )
        body = rewrite_public_markdown(
            str((project.get("document") or {}).get("content") or ""),
            project_id=project_id,
            slug=slug,
            namespace=namespace,
            artifacts=artifacts,
            public_artifact_ids=public_ids,
        )
        content = render_front_matter(
            project=project, paper=paper, publication=publication, slug=slug, cover=cover
        ) + render_public_body(
            markdown=body,
            publication=publication,
            project=project,
            paper=paper,
        )
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        now = _timestamp(self._clock())
        post_rel = PurePosixPath("source", "_posts", namespace, f"{slug}.md")
        files = [post_rel.as_posix()]
        manifest = self._read_manifest()
        projects = manifest.setdefault("projects", {})
        previous = projects.get(project_id)
        old_files = {
            _safe_relative(value).as_posix()
            for value in previous.get("files", [])
            if isinstance(previous, Mapping) and isinstance(value, str)
        } if isinstance(previous, Mapping) else set()
        with tempfile.TemporaryDirectory(prefix=".showcase-stage-", dir=self.root.parent if self.root.parent.exists() else None) as stage_name:
            stage = Path(stage_name)
            staged_post = stage / Path(*post_rel.parts)
            _atomic_write_text(staged_post, content)
            for artifact in artifacts:
                artifact_id = str(artifact.get("id") or "")
                if artifact_id not in public_ids:
                    continue
                source = self._artifact_resolver(str(artifact.get("storageKey") or ""), project_id)
                rel = _public_asset_relative(
                    namespace=namespace,
                    slug=slug,
                    artifact=artifact,
                )
                target = stage / Path(*rel.parts)
                _copy_verified(source, target, int(artifact.get("sizeBytes") or -1), str(artifact.get("sha256") or ""))
                files.append(rel.as_posix())
            # Assets are installed before the post.  The final post replace is
            # therefore the visibility boundary: readers never see a new post
            # that points at files which have not been installed yet.
            for rel in [*files[1:], files[0]]:
                source = stage / Path(*PurePosixPath(rel).parts)
                target = self.root / Path(*PurePosixPath(rel).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
        for rel in old_files.difference(files):
            target = self.root / Path(*_safe_relative(rel).parts)
            if target.is_file() and not target.is_symlink():
                target.unlink()
        projects[project_id] = {
            "slug": slug,
            "kind": project_kind,
            "files": files,
            "contentHash": content_hash,
            "exportedAt": now,
        }
        self._write_manifest(manifest)
        return ShowcaseExportResult(slug, f"/{namespace}/{slug}/", content_hash, tuple(files), now)

    def revoke(self, *, project_id: str) -> tuple[str, ...]:
        manifest = self._read_manifest()
        projects = manifest.setdefault("projects", {})
        entry = projects.pop(project_id, None)
        removed: list[str] = []
        if isinstance(entry, Mapping):
            for value in entry.get("files", []):
                if not isinstance(value, str):
                    continue
                rel = _safe_relative(value)
                target = self.root / Path(*rel.parts)
                if target.is_file() and not target.is_symlink():
                    target.unlink()
                    removed.append(rel.as_posix())
        self._write_manifest(manifest)
        return tuple(removed)

    def _manifest_path(self) -> Path:
        return self.root / ".showcase" / "manifest.json"

    def _read_manifest(self) -> dict[str, object]:
        path = self._manifest_path()
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        if not isinstance(value.get("projects"), dict):
            value["projects"] = {}
        value.setdefault("version", 1)
        return value

    def _write_manifest(self, value: Mapping[str, object]) -> None:
        _atomic_write_text(
            self._manifest_path(),
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )


def _render_results(results: Sequence[Mapping[str, object]]) -> str:
    if not results:
        return "暂无结构化指标记录。"
    lines = ["| 指标 | 原论文 | 复现 | 差值 | 结论 |", "| --- | ---: | ---: | ---: | --- |"]
    for result in results:
        status = CONCLUSION_LABELS.get(str(result.get("status") or ""), str(result.get("status") or ""))
        values = [
            result.get("metricName"),
            result.get("paperValue"),
            result.get("reproductionValue"),
            result.get("differencePercent") or result.get("difference"),
            status,
        ]
        lines.append("| " + " | ".join(_md_cell(value) for value in values) + " |")
    return "\n".join(lines)


def _render_paper_info(
    paper: Mapping[str, object],
    publication: Mapping[str, object],
) -> str:
    title = _md_cell(paper.get("title_zh") or paper.get("title") or "—")
    authors = _md_cell(", ".join(_as_authors(paper.get("authors"))) or "—")
    paper_url = str(publication.get("paperUrl") or paper.get("url") or paper.get("pdf_url") or "").strip()
    code_url = str(publication.get("codeUrl") or "").strip()
    datasets = _string_list(publication.get("datasetUrls"))
    rows = [
        ("论文", title),
        ("作者", authors),
        ("发表", _md_cell(" · ".join(filter(None, [str(paper.get("venue") or ""), str(paper.get("year") or "")])) or "—")),
        ("论文链接", _markdown_link(paper_url, "打开论文")),
        ("代码链接", _markdown_link(code_url, "查看代码")),
        ("数据集", " · ".join(_markdown_link(item, "链接") for item in datasets) or "—"),
    ]
    return "| 字段 | 内容 |\n| --- | --- |\n" + "\n".join(
        f"| {label} | {value} |" for label, value in rows
    )


def _markdown_link(url: str, label: str) -> str:
    return f"[{label}]({url})" if _safe_external_url(url) else "—"


def _render_runs(runs: Sequence[Mapping[str, object]]) -> str:
    if not runs:
        return "暂无实验运行记录。"
    lines: list[str] = []
    for run in runs:
        name = _plain_markdown(str(run.get("name") or run.get("resultSummary") or "实验运行"))
        status = _plain_markdown(str(run.get("status") or ""))
        details = [
            f"状态：{status}",
            f"环境：{_plain_markdown(str(run.get('environment') or '未记录'))}",
            f"数据集：{_plain_markdown(str(run.get('dataset') or '未记录'))}",
            f"代码：{_plain_markdown(str(run.get('codeRevision') or '未记录'))}",
        ]
        summary = _plain_markdown(str(run.get("resultSummary") or "")).strip()
        lines.append(f"- **{name}** · " + " · ".join(details) + (f"\n  {summary}" if summary else ""))
    return "\n".join(lines)


def _as_authors(value: object) -> list[str]:
    if isinstance(value, list):
        return _unique_strings([str(item) for item in value])
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, list):
            return _unique_strings([str(item) for item in decoded])
        return _unique_strings(re.split(r"[,;，；]\s*", value))
    return []


def _safe_external_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not parsed.username


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return _unique_strings([str(item).strip() for item in value if isinstance(item, str) and item.strip()])


def _unique_strings(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        clean = value.strip()
        if clean and clean not in result:
            result.append(clean)
    return result


def _mappings(value: object) -> list[Mapping[str, object]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _valid_digest(value: str) -> bool:
    try:
        validate_sha256(value)
        return True
    except ValueError:
        return False


def _file_digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _copy_verified(source: Path, target: Path, expected_size: int, expected_sha256: str) -> None:
    if not source.is_file() or source.is_symlink() or not _valid_digest(expected_sha256):
        raise PublicationValidationError()
    size, digest = _file_digest(source)
    if size != expected_size or digest != expected_sha256:
        raise PublicationValidationError()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PublicationValidationError()
    return path


def _yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _canonical_markdown(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"


def _md_cell(value: object) -> str:
    return _plain_markdown(str(value if value is not None and str(value).strip() else "—"))\
        .replace("|", "\\|").replace("\n", " ").strip()


def _redact(value: str) -> str:
    return _SECRET.sub(r"\1=[redacted]", value)


def _plain_markdown(value: str) -> str:
    safe = _redact(value)
    safe = _PRIVATE_PATH.sub("[已隐藏本地路径]", safe)
    safe = safe.replace("\\", "\\\\")
    for character in ("*", "_", "[", "]", "<", ">"):
        safe = safe.replace(character, f"\\{character}")
    return safe


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


__all__ = [
    "CONCLUSION_LABELS",
    "PUBLICATION_CONCLUSIONS",
    "PublicationValidation",
    "ShowcaseExportResult",
    "ShowcaseExporter",
    "normalize_slug",
    "render_front_matter",
    "render_public_body",
    "rewrite_public_markdown",
    "validate_publication_snapshot",
]
