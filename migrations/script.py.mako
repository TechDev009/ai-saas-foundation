"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

${upgrades if upgrades else ""}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
