"""Add the optional pinned class-mapping profile to model artifacts.

`backend/app/lifecycle.py` has always required this attribute: `upload_artifact`
accepts a `model_contract_profile` and `release_identity()` reads
`ModelArtifact.model_contract_profile` to record `class_mapping_version` in the
signed release manifest. The column was never created, so every
`POST /api/v1/releases` raised `AttributeError: 'ModelArtifacts' object has no
attribute 'model_contract_profile'` (HTTP 500) and no release could be created.

Nullable on purpose: an artifact that declares no profile keeps resolving its
mapping from the model's own metadata, which is the documented fallback.
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column['name'] for column in sa.inspect(bind).get_columns('model_artifacts')}
    # 0001 builds the schema from the ORM metadata, so a fresh database may already
    # carry the column; adding it twice must stay a no-op rather than a failure.
    if 'model_contract_profile' not in columns:
        op.add_column('model_artifacts', sa.Column('model_contract_profile', sa.String(), nullable=True))


def downgrade():
    raise RuntimeError('Destructive downgrade requires an explicit reviewed migration')
