"""REVIEW.md 修复合集：UNIQUE、JSONB、增量入库列、token 用量列

Revision ID: 002
Revises: 001
Create Date: 2026-05-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = '002'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. card_cache.input_name 加 UNIQUE，避免缓存过期后重复 INSERT 导致脏数据。
    #    先用 DELETE 子查询保留每个 input_name 的最新一行（按 last_fetched_at），再加约束。
    op.execute("""
        DELETE FROM card_cache a
        USING card_cache b
        WHERE a.input_name = b.input_name
          AND (
            COALESCE(a.last_fetched_at, a.created_at) < COALESCE(b.last_fetched_at, b.created_at)
            OR (
                COALESCE(a.last_fetched_at, a.created_at) = COALESCE(b.last_fetched_at, b.created_at)
                AND a.id < b.id
            )
          )
    """)
    op.create_unique_constraint("uq_card_cache_input_name", "card_cache", ["input_name"])

    # 2. rule_chunks 增量入库支持：(source_path, section_id) UNIQUE + content_hash 列
    op.add_column(
        "rule_chunks",
        sa.Column("content_hash", sa.String(64), nullable=True),
    )
    # 历史数据可能存在 (source_path, section_id) 重复（早期 reference/mtr/ipg 用 counter 生成 section_id 不会重，但保险起见去重）
    op.execute("""
        DELETE FROM rule_chunks a
        USING rule_chunks b
        WHERE a.source_path = b.source_path
          AND a.section_id = b.section_id
          AND a.id < b.id
    """)
    op.create_unique_constraint(
        "uq_rule_chunks_source_section", "rule_chunks", ["source_path", "section_id"]
    )

    # 3. metadata 字段从 JSON 升级到 JSONB（GIN 索引能力）
    op.alter_column(
        "rule_chunks",
        "metadata",
        existing_type=sa.JSON(),
        type_=JSONB(),
        postgresql_using="metadata::jsonb",
        existing_nullable=True,
    )
    op.alter_column(
        "card_cache",
        "raw_json",
        existing_type=sa.JSON(),
        type_=JSONB(),
        postgresql_using="raw_json::jsonb",
        existing_nullable=True,
    )
    op.alter_column(
        "judge_queries",
        "used_rules",
        existing_type=sa.JSON(),
        type_=JSONB(),
        postgresql_using="used_rules::jsonb",
        existing_nullable=True,
    )
    op.alter_column(
        "judge_queries",
        "used_cards",
        existing_type=sa.JSON(),
        type_=JSONB(),
        postgresql_using="used_cards::jsonb",
        existing_nullable=True,
    )

    # 4. judge_queries 加 token 用量、tool 轮次、request_id 列，便于成本核算与全链路追踪
    op.add_column("judge_queries", sa.Column("request_id", sa.String(64), nullable=True))
    op.add_column("judge_queries", sa.Column("prompt_tokens", sa.Integer(), nullable=True))
    op.add_column("judge_queries", sa.Column("completion_tokens", sa.Integer(), nullable=True))
    op.add_column("judge_queries", sa.Column("total_tokens", sa.Integer(), nullable=True))
    op.add_column("judge_queries", sa.Column("tool_rounds", sa.Integer(), nullable=True))
    op.create_index("ix_judge_queries_request_id", "judge_queries", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_judge_queries_request_id", table_name="judge_queries")
    op.drop_column("judge_queries", "tool_rounds")
    op.drop_column("judge_queries", "total_tokens")
    op.drop_column("judge_queries", "completion_tokens")
    op.drop_column("judge_queries", "prompt_tokens")
    op.drop_column("judge_queries", "request_id")

    op.alter_column(
        "judge_queries", "used_cards",
        existing_type=JSONB(), type_=sa.JSON(),
        postgresql_using="used_cards::json", existing_nullable=True,
    )
    op.alter_column(
        "judge_queries", "used_rules",
        existing_type=JSONB(), type_=sa.JSON(),
        postgresql_using="used_rules::json", existing_nullable=True,
    )
    op.alter_column(
        "card_cache", "raw_json",
        existing_type=JSONB(), type_=sa.JSON(),
        postgresql_using="raw_json::json", existing_nullable=True,
    )
    op.alter_column(
        "rule_chunks", "metadata",
        existing_type=JSONB(), type_=sa.JSON(),
        postgresql_using="metadata::json", existing_nullable=True,
    )

    op.drop_constraint("uq_rule_chunks_source_section", "rule_chunks", type_="unique")
    op.drop_column("rule_chunks", "content_hash")

    op.drop_constraint("uq_card_cache_input_name", "card_cache", type_="unique")
