"""modify_farm_table

Revision ID: 1d73843703b7
Revises: f0e3d74ff5c8
Create Date: 2025-07-03 00:16:28.261475

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1d73843703b7'
down_revision: Union[str, Sequence[str], None] = 'f0e3d74ff5c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # Drop existing columns
    op.execute("ALTER TABLE public.farm DROP COLUMN IF EXISTS area")
    op.execute("ALTER TABLE public.farm DROP COLUMN IF EXISTS center")
    op.execute("ALTER TABLE public.farm DROP COLUMN IF EXISTS lat")
    op.execute("ALTER TABLE public.farm DROP COLUMN IF EXISTS lon")

    # Re-add as generated columns
    op.execute("""
        ALTER TABLE public.farm
        ADD COLUMN area double precision
        GENERATED ALWAYS AS (
            (ST_Area(ST_SetSRID(geometry, 4326)::geography) * 0.0002471054)
        ) STORED
    """)

    op.execute("""
        ALTER TABLE public.farm
        ADD COLUMN center geometry(Point, 4326)
        GENERATED ALWAYS AS (
            ST_Centroid(geometry)
        ) STORED
    """)

    op.execute("""
        ALTER TABLE public.farm
        ADD COLUMN lat double precision
        GENERATED ALWAYS AS (
            ST_Y(ST_Centroid(geometry))
        ) STORED
    """)

    op.execute("""
        ALTER TABLE public.farm
        ADD COLUMN lon double precision
        GENERATED ALWAYS AS (
            ST_X(ST_Centroid(geometry))
        ) STORED
    """)

def downgrade():
    # Drop the generated columns
    op.execute("ALTER TABLE public.farm DROP COLUMN lon")
    op.execute("ALTER TABLE public.farm DROP COLUMN lat")
    op.execute("ALTER TABLE public.farm DROP COLUMN center")
    op.execute("ALTER TABLE public.farm DROP COLUMN area")

    # Re-add them as normal columns (nullable)
    op.add_column('farm', sa.Column('area', sa.Float(), nullable=True))
    op.add_column('farm', sa.Column('center', sa.types.UserDefinedType(name='geometry'), nullable=True))
    op.add_column('farm', sa.Column('lat', sa.Float(), nullable=True))
    op.add_column('farm', sa.Column('lon', sa.Float(), nullable=True))
