"""add independent candidate research memories and embedding jobs

Revision ID: 20260727_22
Revises: 20260727_21
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from experiment_guardian.infrastructure.models.base import VectorType

revision: str = "20260727_22"
down_revision: str | Sequence[str] | None = "20260727_21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_research_memories",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.String(length=16), nullable=False),
        sa.Column(
            "memory_type",
            sa.Enum(
                "RESEARCH_SYNTHESIS",
                "CONFLICT",
                "OPEN_QUESTION",
                "RECOMMENDATION",
                name="research_memory_type",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "CANDIDATE",
                name="research_memory_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=False),
        sa.Column("citation_ids", sa.JSON(), nullable=False),
        sa.Column("experiment_ids", sa.JSON(), nullable=False),
        sa.Column("protocols", sa.JSON(), nullable=False),
        sa.Column("source_references", sa.JSON(), nullable=False),
        sa.Column("report_source_hash", sa.String(length=64), nullable=False),
        sa.Column("report_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding_document", sa.Text(), nullable=False),
        sa.Column("document_version", sa.String(length=100), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(report_source_hash) = 64",
            name="ck_agent_research_memories_report_source_hash_length",
        ),
        sa.CheckConstraint(
            "length(report_payload_hash) = 64",
            name="ck_agent_research_memories_report_payload_hash_length",
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64",
            name="ck_agent_research_memories_content_hash_length",
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["report_id"], ["agent_research_reports.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "report_id", "finding_id", name="uq_agent_research_memory_finding"
        ),
    )
    op.create_index(
        "ix_agent_research_memories_filter",
        "agent_research_memories",
        ["project_id", "status", "memory_type", "created_at"],
    )

    op.create_table(
        "agent_research_memory_embeddings",
        sa.Column("memory_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model_id", sa.String(length=500), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("document_version", sa.String(length=100), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("embedding", VectorType(1024)),
        sa.Column("normalized", sa.Boolean(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "RETRYABLE_FAILURE",
                "READY",
                "FAILED",
                "DEAD_LETTER",
                name="research_memory_embedding_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("lease_owner", sa.String(length=300)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.JSON()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "dimension = 1024",
            name="ck_agent_research_memory_embeddings_dimension_1024",
        ),
        sa.CheckConstraint(
            "length(input_sha256) = 64",
            name="ck_agent_research_memory_embeddings_input_sha256_length",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_agent_research_memory_embeddings_attempt_count_nonnegative",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1",
            name="ck_agent_research_memory_embeddings_max_attempts_positive",
        ),
        sa.CheckConstraint(
            "generation >= 0",
            name="ck_agent_research_memory_embeddings_generation_nonnegative",
        ),
        sa.CheckConstraint(
            "status != 'READY' OR (embedding IS NOT NULL AND normalized)",
            name="ck_agent_research_memory_embeddings_ready_output_complete",
        ),
        sa.ForeignKeyConstraint(["memory_id"], ["agent_research_memories.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "memory_id",
            "provider",
            "model_id",
            "document_version",
            name="uq_agent_research_memory_embedding_version",
        ),
    )
    op.create_index(
        "ix_agent_research_memory_embeddings_claim",
        "agent_research_memory_embeddings",
        ["status", "available_at", "lease_expires_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_research_memory_embeddings_claim",
        table_name="agent_research_memory_embeddings",
    )
    op.drop_table("agent_research_memory_embeddings")
    op.drop_index(
        "ix_agent_research_memories_filter",
        table_name="agent_research_memories",
    )
    op.drop_table("agent_research_memories")
