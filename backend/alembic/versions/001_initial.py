"""初始迁移：创建 rule_chunks、card_cache、judge_queries 表

Revision ID: 001
Revises:
Create Date: 2026-05-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        'rule_chunks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('document_type', sa.String(32), nullable=False),
        sa.Column('source_path', sa.Text(), nullable=False),
        sa.Column('section_id', sa.String(128), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('embedding', Vector(1024), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_rule_chunks_section_id', 'rule_chunks', ['section_id'])
    op.create_index('ix_rule_chunks_document_type', 'rule_chunks', ['document_type'])
    op.execute("CREATE INDEX ix_rule_chunks_embedding ON rule_chunks USING hnsw (embedding vector_cosine_ops)")

    op.create_table(
        'card_cache',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('input_name', sa.String(256), nullable=False),
        sa.Column('resolved_zh_name', sa.String(256), nullable=True),
        sa.Column('oracle_name', sa.String(256), nullable=True),
        sa.Column('scryfall_id', sa.String(64), nullable=True),
        sa.Column('oracle_text', sa.Text(), nullable=True),
        sa.Column('type_line', sa.String(256), nullable=True),
        sa.Column('mana_cost', sa.String(64), nullable=True),
        sa.Column('raw_json', sa.JSON(), nullable=True),
        sa.Column('last_fetched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_card_cache_input_name', 'card_cache', ['input_name'])

    op.create_table(
        'judge_queries',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('model', sa.String(128), nullable=False),
        sa.Column('confidence', sa.String(16), nullable=False),
        sa.Column('used_rules', sa.JSON(), nullable=True),
        sa.Column('used_cards', sa.JSON(), nullable=True),
        sa.Column('latency_ms', sa.Float(), nullable=True),
        sa.Column('reasoning_summary', sa.Text(), nullable=True),
        sa.Column('needs_human_judge', sa.Boolean(), default=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_judge_queries_created_at', 'judge_queries', ['created_at'])
    op.create_index('ix_judge_queries_needs_human_judge', 'judge_queries', ['needs_human_judge'])


def downgrade() -> None:
    op.drop_table('judge_queries')
    op.drop_table('card_cache')
    op.drop_table('rule_chunks')
