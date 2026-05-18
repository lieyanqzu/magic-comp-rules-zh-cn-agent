"""初始迁移：rule_chunks、card_cache、judge_queries

Revision ID: 001
Revises:
Create Date: 2026-05-17

合并版（项目尚未上线，002 已折叠进来）：
- rule_chunks 直接带 content_hash + UNIQUE(source_path, section_id)
- card_cache.input_name 直接 UNIQUE
- judge_queries 直接带 request_id / token 用量 / tool_rounds 列
- JSONB 列从一开始就用 postgresql.JSONB()
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector

revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        'rule_chunks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('document_type', sa.String(32), nullable=False),
        sa.Column('source_path', sa.Text(), nullable=False),
        sa.Column('section_id', sa.String(128), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=True),
        sa.Column('embedding', Vector(1024), nullable=True),
        sa.Column('metadata', JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source_path', 'section_id', name='uq_rule_chunks_source_section'),
    )
    op.create_index('ix_rule_chunks_section_id', 'rule_chunks', ['section_id'])
    op.create_index('ix_rule_chunks_document_type', 'rule_chunks', ['document_type'])
    op.execute("CREATE INDEX ix_rule_chunks_embedding ON rule_chunks USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX ix_rule_chunks_title_trgm ON rule_chunks USING gin (title gin_trgm_ops)")
    op.execute("CREATE INDEX ix_rule_chunks_content_trgm ON rule_chunks USING gin (content gin_trgm_ops)")

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
        sa.Column('raw_json', JSONB(), nullable=True),
        sa.Column('last_fetched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('input_name', name='uq_card_cache_input_name'),
    )
    op.create_index('ix_card_cache_input_name', 'card_cache', ['input_name'])

    op.create_table(
        'judge_queries',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('request_id', sa.String(64), nullable=True),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('model', sa.String(128), nullable=False),
        sa.Column('confidence', sa.String(16), nullable=False),
        sa.Column('used_rules', JSONB(), nullable=True),
        sa.Column('used_cards', JSONB(), nullable=True),
        sa.Column('latency_ms', sa.Float(), nullable=True),
        sa.Column('prompt_tokens', sa.Integer(), nullable=True),
        sa.Column('completion_tokens', sa.Integer(), nullable=True),
        sa.Column('total_tokens', sa.Integer(), nullable=True),
        sa.Column('tool_rounds', sa.Integer(), nullable=True),
        sa.Column('reasoning_summary', sa.Text(), nullable=True),
        sa.Column('needs_human_judge', sa.Boolean(), default=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_judge_queries_created_at', 'judge_queries', ['created_at'])
    op.create_index('ix_judge_queries_needs_human_judge', 'judge_queries', ['needs_human_judge'])
    op.create_index('ix_judge_queries_request_id', 'judge_queries', ['request_id'])


def downgrade() -> None:
    op.drop_table('judge_queries')
    op.drop_table('card_cache')
    op.drop_table('rule_chunks')
