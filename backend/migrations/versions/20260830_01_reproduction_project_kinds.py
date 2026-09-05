"""Allow the reproduction workspace to host article and blog projects."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260830_01"
down_revision: str | None = "20260829_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reproduction_projects",
        sa.Column(
            "project_kind",
            sa.Text(),
            nullable=False,
            server_default="reproduction",
        ),
    )
    op.create_index(
        "ix_reproduction_projects_kind_updated",
        "reproduction_projects",
        ["project_kind", "updated_at", "id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    article_count = connection.execute(
        sa.text(
            "SELECT count(*) FROM reproduction_projects "
            "WHERE project_kind <> 'reproduction'"
        )
    ).scalar_one()
    if article_count:
        raise RuntimeError(
            "REPRODUCTION_PROJECT_KIND_DOWNGRADE_UNSAFE: "
            "article or blog projects exist"
        )
    op.drop_index(
        "ix_reproduction_projects_kind_updated",
        table_name="reproduction_projects",
    )
    op.drop_column("reproduction_projects", "project_kind")
