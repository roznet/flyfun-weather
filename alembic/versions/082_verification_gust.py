"""Persist wind gust in verification scoring (#491).

Only wind *speed* was scored. Gust was never persisted anywhere permanent:
the model gust lives on ``airport_forecast_snapshots.wind_gusts_10m_kt``,
which is pruned at >10 days, so the "does the model over-call gusts?"
question could only ever be asked about the last week and a half.

Raw scores
----------
``verification_scores`` gains the forecast gust itself, not just a delta.
The observed gust is NULL on ~90% of hours (a METAR only carries a gust
group when the peak exceeds the mean by ~10 kt), so a delta alone would
drop exactly the rows that answer the question — "the model called a gust
and the airport wasn't gusting". ``model_gust_flag`` stores that same
~10 kt criterion applied to the forecast so rollups can SUM it without
carrying model wind speed.

``taf_verification_scores`` gains the delta only: the TAF gust already
lives permanently on ``verification_observations.taf_wind_gust_kt``.

Rollups
-------
Daily gets additive SUM/count columns for the two conditionings that must
never be blended (forecast-flagged vs obs-flagged — see
``tasks/verification_gust``); monthly gets the MAE/bias equivalents.
Existing rollup rows keep 0/NULL in the new columns until their day/month
is re-rolled (``verify rollup-daily-stats --rebuild``).

Revision ID: 082
Revises: 081
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "082"
down_revision: Union[str, None] = "081"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("verification_scores") as batch_op:
        batch_op.add_column(
            sa.Column("model_wind_gust_kt", sa.Float(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("wind_gust_delta_kt", sa.Float(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("model_gust_flag", sa.Boolean(), nullable=True)
        )

    with op.batch_alter_table("taf_verification_scores") as batch_op:
        batch_op.add_column(
            sa.Column("wind_gust_delta_kt", sa.Float(), nullable=True)
        )

    with op.batch_alter_table("verification_daily_stats") as batch_op:
        batch_op.add_column(
            sa.Column(
                "n_gust", sa.Integer(), nullable=False, server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("sum_abs_gust_delta_kt", sa.Float(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("sum_gust_delta_kt", sa.Float(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "n_gust_flagged_peak", sa.Integer(), nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("sum_gust_flagged_over_peak_kt", sa.Float(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "n_model_gust_flag", sa.Integer(), nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "n_obs_gust", sa.Integer(), nullable=False, server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "n_gust_flag_hit", sa.Integer(), nullable=False,
                server_default="0",
            )
        )

    with op.batch_alter_table("verification_monthly_stats") as batch_op:
        batch_op.add_column(
            sa.Column("wind_gust_mae_kt", sa.Float(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("wind_gust_bias_kt", sa.Float(), nullable=True)
        )
        batch_op.add_column(sa.Column("n_gust", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("gust_over_peak_bias_kt", sa.Float(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("n_gust_flagged_peak", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("n_model_gust_flag", sa.Integer(), nullable=True)
        )
        batch_op.add_column(sa.Column("n_obs_gust", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("n_gust_flag_hit", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("verification_monthly_stats") as batch_op:
        for col in (
            "n_gust_flag_hit",
            "n_obs_gust",
            "n_model_gust_flag",
            "n_gust_flagged_peak",
            "gust_over_peak_bias_kt",
            "n_gust",
            "wind_gust_bias_kt",
            "wind_gust_mae_kt",
        ):
            batch_op.drop_column(col)

    with op.batch_alter_table("verification_daily_stats") as batch_op:
        for col in (
            "n_gust_flag_hit",
            "n_obs_gust",
            "n_model_gust_flag",
            "sum_gust_flagged_over_peak_kt",
            "n_gust_flagged_peak",
            "sum_gust_delta_kt",
            "sum_abs_gust_delta_kt",
            "n_gust",
        ):
            batch_op.drop_column(col)

    with op.batch_alter_table("taf_verification_scores") as batch_op:
        batch_op.drop_column("wind_gust_delta_kt")

    with op.batch_alter_table("verification_scores") as batch_op:
        for col in ("model_gust_flag", "wind_gust_delta_kt", "model_wind_gust_kt"):
            batch_op.drop_column(col)
