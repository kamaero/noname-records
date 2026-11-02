"""бот: демо дикторов, пересланные владельцем (проект Б, 23.10)

Граббер на Telethon входил в личный аккаунт владельца — риск бана. Теперь владелец пересылает
боту сообщение диктора с демо. `bot_demo_intakes` — «приём»: один вопрос об имени на пачку от
одного отправителя; `bot_demo_items` — каждое пересланное демо, ссылка или текст, с состоянием,
чтобы повтор вебхука и двойное нажатие не давали второе демо.

Revision ID: 0052_bot_demos
Revises: 0051_audition_comment
Create Date: 2026-10-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0055_bot_demos"
down_revision: Union[str, None] = "0054_audition_comment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "bot_demo_intakes" not in tables:
        op.create_table(
            "bot_demo_intakes",
            sa.Column("id", sa.String(12), primary_key=True),
            sa.Column("sender_key", sa.String(160), nullable=False),
            sa.Column("telegram_user_id", sa.String(32), nullable=False, server_default=""),
            sa.Column("tg_name", sa.String(120), nullable=False, server_default=""),
            sa.Column("tg_username", sa.String(64), nullable=False, server_default=""),
            sa.Column("state", sa.String(12), nullable=False, server_default="ask"),
            sa.Column("pending_name", sa.String(120), nullable=False, server_default=""),
            sa.Column("foreign_note", sa.String(200), nullable=False, server_default=""),
            sa.Column("user_id", sa.String(36), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_bot_demo_intakes_state", "bot_demo_intakes", ["state"])
    if "bot_demo_items" not in tables:
        op.create_table(
            "bot_demo_items",
            sa.Column("id", sa.String(12), primary_key=True),
            sa.Column("intake_id", sa.String(12), nullable=False),
            sa.Column("source_message_id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(8), nullable=False),
            sa.Column("ref", sa.String(1024), nullable=False, server_default=""),
            sa.Column("file_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("mime_type", sa.String(80), nullable=False, server_default=""),
            sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("text", sa.Text(), nullable=False, server_default=""),
            sa.Column("state", sa.String(12), nullable=False, server_default="waiting"),
            sa.Column("result", sa.String(255), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("uq_bot_demo_items_msg", "bot_demo_items", ["source_message_id", "kind", "ref"], unique=True)
        op.create_index("ix_bot_demo_items_intake", "bot_demo_items", ["intake_id"])


def downgrade() -> None:
    tables = _tables()
    if "bot_demo_items" in tables:
        op.drop_index("ix_bot_demo_items_intake", table_name="bot_demo_items")
        op.drop_index("uq_bot_demo_items_msg", table_name="bot_demo_items")
        op.drop_table("bot_demo_items")
    if "bot_demo_intakes" in tables:
        op.drop_index("ix_bot_demo_intakes_state", table_name="bot_demo_intakes")
        op.drop_table("bot_demo_intakes")
