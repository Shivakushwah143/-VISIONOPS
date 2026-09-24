"""Index devices.credential_hash; device authentication needs it at fleet scale.

Every authenticated device request resolves the caller with
`SELECT ... FROM devices WHERE credential_hash = %s` (`backend/app/security.py`).
The column carried no index, so at fleet scale each heartbeat, desired-state poll
and artifact download sequentially scanned the whole devices table. With 10 000
logical devices that is millions of wasted row reads per heartbeat round and it was
the dominant database cost while the fleet was being exercised.

Partial-lineage note: the index is not unique because existing rows may legitimately
hold a NULL credential (a registered but never-enrolled device).
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

INDEX = 'ix_devices_credential_hash'


def upgrade():
    bind = op.get_bind()
    existing = {index['name'] for index in sa.inspect(bind).get_indexes('devices')}
    # 0001 builds the schema from the ORM metadata, so a fresh database may already
    # carry the index; creating it twice must stay a no-op rather than a failure.
    if INDEX not in existing:
        op.create_index(INDEX, 'devices', ['credential_hash'])


def downgrade():
    raise RuntimeError('Destructive downgrade requires an explicit reviewed migration')
