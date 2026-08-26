"""Add experiment timestamps and reproducibility result comparisons."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260826_01"
down_revision: str | None = "20260825_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("experiment_runs", sa.Column("name", sa.Text(), nullable=True))
    op.add_column("experiment_runs", sa.Column("started_at", sa.Text(), nullable=True))
    op.add_column("experiment_runs", sa.Column("finished_at", sa.Text(), nullable=True))
    op.add_column("experiment_runs", sa.Column("runtime_versions", sa.Text(), nullable=True))
    op.add_column("experiment_runs", sa.Column("dataset", sa.Text(), nullable=True))
    op.add_column("experiment_runs", sa.Column("preprocessing", sa.Text(), nullable=True))
    op.add_column("experiment_runs", sa.Column("repository_url", sa.Text(), nullable=True))
    op.add_column("experiment_runs", sa.Column("config", sa.Text(), nullable=True))
    op.add_column("experiment_runs", sa.Column("issues", sa.Text(), nullable=True))
    op.create_table(
        "reproduction_results",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("paper_value", sa.Text(), nullable=True),
        sa.Column("reproduction_value", sa.Text(), nullable=True),
        sa.Column("difference", sa.Text(), nullable=True),
        sa.Column("difference_percent", sa.Text(), nullable=True),
        sa.Column("dataset_settings", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="not_reproduced"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.PrimaryKeyConstraint("id", name="pk_reproduction_results"),
        sa.ForeignKeyConstraint(["project_id"], ["reproduction_projects.id"], ondelete="CASCADE", name="fk_reproduction_results_project"),
        sa.CheckConstraint("length(trim(metric_name)) > 0", name="ck_reproduction_results_metric"),
        sa.CheckConstraint("status IN ('reproduced','partial','not_reproduced','inconsistent')", name="ck_reproduction_results_status"),
    )
    op.create_index("ix_reproduction_results_project", "reproduction_results", ["project_id", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_reproduction_results_project", table_name="reproduction_results")
    op.drop_table("reproduction_results")
    op.drop_column("experiment_runs", "finished_at")
    op.drop_column("experiment_runs", "started_at")
    op.drop_column("experiment_runs", "issues")
    op.drop_column("experiment_runs", "config")
    op.drop_column("experiment_runs", "repository_url")
    op.drop_column("experiment_runs", "preprocessing")
    op.drop_column("experiment_runs", "dataset")
    op.drop_column("experiment_runs", "runtime_versions")
    op.drop_column("experiment_runs", "name")
