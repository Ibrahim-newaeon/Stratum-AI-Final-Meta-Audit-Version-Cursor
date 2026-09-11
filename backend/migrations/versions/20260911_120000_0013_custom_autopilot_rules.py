"""Create Custom Autopilot rules + execution audit tables.

Revision ID: 0013_custom_autopilot_rules
Revises: 0012_tenant_whatsapp_credentials
Create Date: 2026-09-11

Custom Autopilot rules persist if/then definitions that enqueue
``fact_actions_queue`` rows. They never call Meta write_client directly.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_custom_autopilot_rules"
down_revision = "0012_tenant_whatsapp_credentials"
branch_labels = None
depends_on = None

RULES = "custom_autopilot_rules"
EXECS = "custom_autopilot_rule_executions"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if RULES not in tables:
        op.create_table(
            RULES,
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
                nullable=False,
            ),
            sa.Column(
                "tenant_id",
                sa.Integer(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'draft'"),
            ),
            sa.Column(
                "conditions",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "actions",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "require_approval",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            sa.Column(
                "cooldown_hours",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("24"),
            ),
            sa.Column(
                "max_executions_per_day",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("10"),
            ),
            sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "trigger_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "is_deleted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.create_index(
            "ix_custom_autopilot_rules_tenant_status",
            RULES,
            ["tenant_id", "status"],
        )

    tables = set(sa.inspect(bind).get_table_names())
    if EXECS not in tables:
        op.create_table(
            EXECS,
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
                nullable=False,
            ),
            sa.Column(
                "tenant_id",
                sa.Integer(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "rule_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("custom_autopilot_rules.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("campaign_id", sa.Integer(), nullable=True),
            sa.Column("gate_decision", sa.String(length=32), nullable=False),
            sa.Column("gate_reason", sa.Text(), nullable=True),
            sa.Column(
                "matched",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "enqueued",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column("queued_action_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("action_type", sa.String(length=100), nullable=True),
            sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("signal_health_score", sa.Float(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.create_index(
            "ix_custom_autopilot_exec_tenant_created",
            EXECS,
            ["tenant_id", "created_at"],
        )
        op.create_index(
            "ix_custom_autopilot_exec_rule",
            EXECS,
            ["rule_id", "created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if EXECS in tables:
        op.drop_table(EXECS)
    if RULES in tables:
        op.drop_table(RULES)
