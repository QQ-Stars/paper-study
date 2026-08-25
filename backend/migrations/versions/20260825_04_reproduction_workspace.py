"""Add durable paper reproduction workspace data."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260825_04"
down_revision: str | None = "20260807_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reproduction_projects",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("paper_id", sa.Text(), nullable=True),
        sa.Column("paper_title", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="planned"),
        sa.Column("tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.PrimaryKeyConstraint("id", name="pk_reproduction_projects"),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="SET NULL", name="fk_reproduction_projects_paper"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_reproduction_projects_name"),
        sa.CheckConstraint("length(trim(paper_title)) > 0", name="ck_reproduction_projects_paper_title"),
        sa.CheckConstraint("status IN ('planned','preparing','running','completed','blocked','archived')", name="ck_reproduction_projects_status"),
        sa.CheckConstraint("revision >= 1", name="ck_reproduction_projects_revision"),
    )
    op.create_index("ix_reproduction_projects_updated", "reproduction_projects", ["updated_at", "id"])
    op.create_index("ix_reproduction_projects_status", "reproduction_projects", ["status", "updated_at"])

    op.create_table(
        "reproduction_documents",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("save_status", sa.Text(), nullable=False, server_default="saved"),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.PrimaryKeyConstraint("id", name="pk_reproduction_documents"),
        sa.ForeignKeyConstraint(["project_id"], ["reproduction_projects.id"], ondelete="CASCADE", name="fk_reproduction_documents_project"),
        sa.UniqueConstraint("project_id", name="uq_reproduction_documents_project"),
        sa.CheckConstraint("revision >= 1", name="ck_reproduction_documents_revision"),
        sa.CheckConstraint("save_status IN ('saved','saving','failed')", name="ck_reproduction_documents_save_status"),
    )

    op.create_table(
        "experiment_runs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=True),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column("parameters_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("data_version", sa.Text(), nullable=True),
        sa.Column("code_revision", sa.Text(), nullable=True),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="planned"),
        sa.Column("metrics_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_runs"),
        sa.ForeignKeyConstraint(["project_id"], ["reproduction_projects.id"], ondelete="CASCADE", name="fk_experiment_runs_project"),
        sa.CheckConstraint("status IN ('planned','running','completed','failed','blocked')", name="ck_experiment_runs_status"),
    )
    op.create_index("ix_experiment_runs_project", "experiment_runs", ["project_id", "created_at"])

    op.create_table(
        "reproduction_artifacts",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.PrimaryKeyConstraint("id", name="pk_reproduction_artifacts"),
        sa.ForeignKeyConstraint(["project_id"], ["reproduction_projects.id"], ondelete="CASCADE", name="fk_reproduction_artifacts_project"),
        sa.ForeignKeyConstraint(["run_id"], ["experiment_runs.id"], ondelete="SET NULL", name="fk_reproduction_artifacts_run"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_reproduction_artifacts_size"),
        sa.CheckConstraint("length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'", name="ck_reproduction_artifacts_sha"),
    )
    op.create_index("ix_reproduction_artifacts_project", "reproduction_artifacts", ["project_id", "created_at"])

    op.create_table(
        "reproduction_notes",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.PrimaryKeyConstraint("id", name="pk_reproduction_notes"),
        sa.ForeignKeyConstraint(["project_id"], ["reproduction_projects.id"], ondelete="CASCADE", name="fk_reproduction_notes_project"),
    )
    op.create_index("ix_reproduction_notes_project", "reproduction_notes", ["project_id", "updated_at"])


def downgrade() -> None:
    connection = op.get_bind()
    tables = (
        "reproduction_notes",
        "reproduction_artifacts",
        "experiment_runs",
        "reproduction_documents",
        "reproduction_projects",
    )
    nonempty = [
        name for name in tables
        if connection.execute(sa.text(f'SELECT count(*) FROM "{name}"')).scalar_one() != 0
    ]
    if nonempty:
        raise RuntimeError(
            "REPRODUCTION_DOWNGRADE_NONEMPTY: reproduction tables contain data: "
            + ", ".join(nonempty)
        )
    for name in tables:
        op.drop_table(name)
