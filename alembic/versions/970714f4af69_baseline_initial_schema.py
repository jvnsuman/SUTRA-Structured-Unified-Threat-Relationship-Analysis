"""baseline: initial schema

Revision ID: 970714f4af69
Revises: 
Create Date: 2026-09-12 15:25:21.923136

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '970714f4af69'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _missing(name: str) -> bool:
    return not sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Create the ORIGINAL schema (before any later migration).

    This baseline used to be empty because the first database was built
    with Base.metadata.create_all(), which meant `alembic upgrade head`
    failed on any fresh database ("no such table: source_documents").
    Each table is created only if absent, so databases that already exist
    (created by create_all or by an earlier deploy) are left untouched.
    Columns added by later revisions (users.preferences,
    cases.description/confidentiality, source_documents.structured) are
    intentionally NOT here -- those revisions add them.
    """
    if _missing("agencies"):
        op.create_table(
            "agencies",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("agency_type", sa.String(), nullable=False),
            sa.Column("parent_agency_id", sa.String(), nullable=True),
            sa.Column("is_active", sa.String(), nullable=True),
            sa.ForeignKeyConstraint(["parent_agency_id"], ["agencies.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    if _missing("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("badge_id", sa.String(), nullable=False),
            sa.Column("agency_id", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("password_hash", sa.String(), nullable=True),
            sa.Column("last_login", sa.String(), nullable=True),
            sa.ForeignKeyConstraint(["agency_id"], ["agencies.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_users_badge_id", "users", ["badge_id"], unique=True)
    if _missing("cases"):
        op.create_table(
            "cases",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("agency_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="open"),
            sa.Column("created_by_user_id", sa.String(), nullable=True),
            sa.Column("opened_at", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(["agency_id"], ["agencies.id"]),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    if _missing("case_investigators"):
        op.create_table(
            "case_investigators",
            sa.Column("case_id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("case_id", "user_id"),
        )
    if _missing("source_documents"):
        op.create_table(
            "source_documents",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("document_type", sa.String(), nullable=False),
            sa.Column("raw_text", sa.String(), nullable=False),
            sa.Column("case_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    if _missing("entities"):
        op.create_table(
            "entities",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("entity_type", sa.String(), nullable=False),
            sa.Column("raw_text", sa.String(), nullable=False),
            sa.Column("normalized_text", sa.String(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("source_document_id", sa.String(), nullable=True),
            sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    if _missing("relationships"):
        op.create_table(
            "relationships",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("source_entity_id", sa.String(), nullable=False),
            sa.Column("target_entity_id", sa.String(), nullable=False),
            sa.Column("relationship_type", sa.String(), nullable=False),
            sa.Column("source_document_id", sa.String(), nullable=True),
            sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
            sa.ForeignKeyConstraint(["source_entity_id"], ["entities.id"]),
            sa.ForeignKeyConstraint(["target_entity_id"], ["entities.id"]),
            sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    if _missing("reports"):
        op.create_table(
            "reports",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("case_id", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("format", sa.String(), nullable=False),
            sa.Column("content", sa.String(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    # The baseline is never downgraded past: dropping it would drop all data.
    pass
