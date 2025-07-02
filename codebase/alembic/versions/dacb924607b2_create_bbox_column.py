"""Create_bbox_column

Revision ID: dacb924607b2
Revises: 30f1d6f25759
Create Date: 2025-07-01 10:04:19.640055

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dacb924607b2'
down_revision: Union[str, Sequence[str], None] = '30f1d6f25759'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # Add bbox column
    op.execute(
        """
        ALTER TABLE farm ADD COLUMN bbox JSONB;
        """
    )

    # Create trigger function
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_bbox()
        RETURNS trigger AS $$
        BEGIN
          NEW.bbox := to_jsonb(ARRAY[
            ST_XMin(NEW.geometry),
            ST_YMin(NEW.geometry),
            ST_XMax(NEW.geometry),
            ST_YMax(NEW.geometry)
          ]);
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # Create trigger
    op.execute(
        """
        CREATE TRIGGER set_bbox
        BEFORE INSERT OR UPDATE ON farm
        FOR EACH ROW
        EXECUTE FUNCTION update_bbox();
        """
    )

    # Populate existing rows
    op.execute(
        """
        UPDATE farm
        SET bbox = to_jsonb(ARRAY[
            ST_XMin(geometry),
            ST_YMin(geometry),
            ST_XMax(geometry),
            ST_YMax(geometry)
        ]);
        """
    )


def downgrade():
    op.execute(
        """
        DROP TRIGGER IF EXISTS set_bbox ON farm;
        """
    )
    op.execute(
        """
        DROP FUNCTION IF EXISTS update_bbox;
        """
    )
    op.execute(
        """
        ALTER TABLE farm DROP COLUMN bbox;
        """
    )
