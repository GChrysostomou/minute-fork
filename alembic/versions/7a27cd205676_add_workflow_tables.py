"""add_workflow_tables

Revision ID: 7a27cd205676
Revises: c4f9d2a1b8e3
Create Date: 2026-07-15 11:34:03.103770

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7a27cd205676"
down_revision: Union[str, None] = "c4f9d2a1b8e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_run",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_datetime", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_datetime", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("transcription_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "AWAITING_START",
                "IN_PROGRESS",
                "AWAITING_CONFIRMATION",
                "COMPLETED",
                "FAILED",
                name="workflowstatus",
            ),
            server_default="AWAITING_START",
            nullable=False,
        ),
        sa.Column("error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["transcription_id"], ["transcription.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_run_transcription_id", "workflow_run", ["transcription_id"])
    op.create_index("ix_workflow_run_user_id", "workflow_run", ["user_id"])

    op.create_table(
        "workflow_action",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_datetime", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_datetime", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("action_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "status",
            sa.Enum("PENDING", "APPROVED", "REJECTED", "COMPLETED", "FAILED", name="workflowactionstatus"),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("result_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "workflow_credential",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_datetime", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_datetime", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("service_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("encrypted_token", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workflow_credential_user_service",
        "workflow_credential",
        ["user_id", "service_name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_credential_user_service", table_name="workflow_credential")
    op.drop_table("workflow_credential")
    op.drop_table("workflow_action")
    op.drop_index("ix_workflow_run_user_id", table_name="workflow_run")
    op.drop_index("ix_workflow_run_transcription_id", table_name="workflow_run")
    op.drop_table("workflow_run")
    # Drop the Postgres enum types created during upgrade.
    sa.Enum(name="workflowactionstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="workflowstatus").drop(op.get_bind(), checkfirst=True)
