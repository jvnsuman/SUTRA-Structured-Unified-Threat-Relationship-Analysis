"""add preferences column to users

Revision ID: b6778e0f22c0
Revises: 970714f4af69
Create Date: 2026-09-12 15:26:36.652067

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6778e0f22c0'
down_revision: Union[str, None] = '970714f4af69'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # This revision was originally an empty stub (the column was created
    # by create_all on the first database), so fresh databases never got
    # users.preferences. Add it only when missing so existing databases,
    # which already have it, are unaffected.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "preferences" not in columns:
        op.add_column("users", sa.Column("preferences", sa.String(), nullable=True))


def downgrade() -> None:
    pass
