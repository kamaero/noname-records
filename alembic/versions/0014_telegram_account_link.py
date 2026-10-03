"""link a whitelisted Telegram identity to the account it belongs to

Telegram login used to resolve a user by the synthetic login `tg_<id>` and create the
row when it was missing. Anyone who also had a password therefore owned two accounts
that nothing connected, and deleting either did not merge them — the Telegram one came
back on the next visit.

The link goes on the whitelist row because that row is already loaded first at login
and already holds the Telegram id; `users` stays free of provider-specific columns.

Additive and guarded like the revisions before it. The backfill points each whitelist
row at the `tg_<id>` account it has been feeding, so the change is invisible to the
25 people who sign in this way: they land exactly where they landed yesterday.

Revision ID: 0014_telegram_account_link
Revises: 0013_auto_publish
Create Date: 2026-09-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_telegram_account_link"
down_revision: Union[str, None] = "0013_auto_publish"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    return {row["name"] for row in sa.inspect(op.get_bind()).get_columns("telegram_auth_accounts")}


def upgrade() -> None:
    if "user_id" not in _columns():
        op.add_column("telegram_auth_accounts", sa.Column("user_id", sa.String(36), nullable=False, server_default=""))
        op.get_bind().execute(
            sa.text(
                """
                update telegram_auth_accounts
                   set user_id = coalesce(
                       (select u.id from users u where u.login = 'tg_' || telegram_auth_accounts.telegram_user_id),
                       ''
                   )
                 where user_id = ''
                """
            )
        )


def downgrade() -> None:
    if "user_id" in _columns():
        op.drop_column("telegram_auth_accounts", "user_id")
