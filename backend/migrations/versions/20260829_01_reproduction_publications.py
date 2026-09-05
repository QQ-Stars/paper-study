"""Add publication decisions and export state for the public showcase."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260829_01"
down_revision: str | None = "20260826_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reproduction_publications",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("stable_slug", sa.Text(), nullable=True),
        sa.Column("public_title", sa.Text(), nullable=True),
        sa.Column("public_summary", sa.Text(), nullable=True),
        sa.Column("aggregate_conclusion", sa.Text(), nullable=True),
        sa.Column("paper_url", sa.Text(), nullable=True),
        sa.Column("code_url", sa.Text(), nullable=True),
        sa.Column("dataset_urls_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("public_artifact_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("validation_passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("validation_errors_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("approved_at", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("last_exported_at", sa.Text(), nullable=True),
        sa.Column("export_error", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.PrimaryKeyConstraint("project_id", name="pk_reproduction_publications"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["reproduction_projects.id"],
            ondelete="CASCADE",
            name="fk_reproduction_publications_project",
        ),
        sa.UniqueConstraint("stable_slug", name="uq_reproduction_publications_slug"),
        sa.CheckConstraint(
            "decision IN ('draft','approved','revoked')",
            name="ck_reproduction_publications_decision",
        ),
        sa.CheckConstraint(
            "status IN ('draft','published','stale','failed','revoked')",
            name="ck_reproduction_publications_status",
        ),
        sa.CheckConstraint(
            "validation_passed IN (0,1)",
            name="ck_reproduction_publications_validation",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_reproduction_publications_revision"),
    )
    op.create_index(
        "ix_reproduction_publications_status",
        "reproduction_publications",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    count = connection.execute(
        sa.text("SELECT count(*) FROM reproduction_publications")
    ).scalar_one()
    if count:
        raise RuntimeError(
            "REPRODUCTION_PUBLICATIONS_DOWNGRADE_NONEMPTY: publication records exist"
        )
    op.drop_index("ix_reproduction_publications_status", table_name="reproduction_publications")
    op.drop_table("reproduction_publications")
