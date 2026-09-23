"""entity metadata, evidence_links, resolution_overrides, audit_log

Revision ID: d1a5e0c7b2f4
Revises: c00e9579a7ea
Create Date: 2026-09-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1a5e0c7b2f4'
down_revision: Union[str, None] = 'c00e9579a7ea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('entities', sa.Column('metadata_json', sa.String(), nullable=True))

    op.create_table(
        'evidence_links',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('entity_id', sa.String(), nullable=False),
        sa.Column('document_id', sa.String(), nullable=False),
        sa.Column('case_id', sa.String(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['source_documents.id']),
        sa.ForeignKeyConstraint(['case_id'], ['cases.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('entity_id', 'document_id', name='uq_evidence_entity_document'),
    )
    op.create_index('ix_evidence_links_entity_id', 'evidence_links', ['entity_id'])
    op.create_index('ix_evidence_links_case_id', 'evidence_links', ['case_id'])

    op.create_table(
        'resolution_overrides',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('case_id', sa.String(), nullable=False),
        sa.Column('mention_a_id', sa.String(), nullable=False),
        sa.Column('mention_b_id', sa.String(), nullable=False),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('note', sa.String(), nullable=True),
        sa.Column('created_by_user_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['case_id'], ['cases.id']),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('case_id', 'mention_a_id', 'mention_b_id', name='uq_override_pair'),
    )
    op.create_index('ix_resolution_overrides_case_id', 'resolution_overrides', ['case_id'])

    op.create_table(
        'audit_log',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('actor_user_id', sa.String(), nullable=True),
        sa.Column('actor_badge_id', sa.String(), nullable=True),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('target_type', sa.String(), nullable=True),
        sa.Column('target_id', sa.String(), nullable=True),
        sa.Column('detail', sa.String(), nullable=True),
        sa.Column('ip_address', sa.String(), nullable=True),
        sa.Column('success', sa.String(), nullable=False, server_default='true'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_audit_log_created_at', 'audit_log', ['created_at'])
    op.create_index('ix_audit_log_action', 'audit_log', ['action'])


def downgrade() -> None:
    op.drop_table('audit_log')
    op.drop_table('resolution_overrides')
    op.drop_table('evidence_links')
    op.drop_column('entities', 'metadata_json')
