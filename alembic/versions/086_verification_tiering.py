"""Verification data tiering: global rollups, TAF daily, activity, archive manifest (#522).

Four new tables, all additive — nothing existing is altered, so this
migration is safe to deploy ahead of any of the #522 phases being enabled.

``verification_global_daily_stats``
    The per-airport daily rollup with ``icao`` dropped: ~90 rows/day instead
    of ~12K, written as a GROUP BY over the per-airport table in the same job.
    Every unfiltered dashboard/digest aggregate reads this once
    ``VERIFICATION_GLOBAL_ROLLUP_READS=1``.

``verification_activity_daily``
    ``COUNT(DISTINCT observation_id)`` per (date, source) — the one activity
    number that structurally cannot survive a per-group rollup, and the reason
    ``get_activity_summary`` was still scanning raw ``verification_scores``.

``taf_verification_daily``
    TAF's own daily rollup keyed on ``days_out = lead_hours // 24``. TAF was
    left out of the #154 rollup because its key shape differs; at 330K rows
    that was affordable at query time and at ~2M it is not.

``archive_manifest``
    Row-count, max-source-id and checksum per archived Parquet period. This is
    the gate Phase 3's pruning consults: no manifest, no delete. ``max_id``
    is what makes the gate exact — keys are monotonic, so a live row with
    ``id > max_id`` is one the archive doesn't have.

Index names are declared identically on the ORM models so dev ``create_all``
and this migration agree.

Revision ID: 086
Revises: 085
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "086"
down_revision: Union[str, None] = "085"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "verification_global_daily_stats",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=20), nullable=False),
        sa.Column("days_out", sa.Integer(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_ceiling", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_wind", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_temp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_vis", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cat_match", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cat_opt_1", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cat_opt_2", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cat_pess_1", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cat_pess_2", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sum_abs_ceiling_delta_ft", sa.Float(), nullable=True),
        sa.Column("sum_ceiling_delta_ft", sa.Float(), nullable=True),
        sa.Column("sum_abs_wind_delta_kt", sa.Float(), nullable=True),
        sa.Column("sum_abs_temp_delta_c", sa.Float(), nullable=True),
        sa.Column("sum_abs_vis_delta_m", sa.Float(), nullable=True),
        sa.Column("n_gust", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sum_abs_gust_delta_kt", sa.Float(), nullable=True),
        sa.Column("sum_gust_delta_kt", sa.Float(), nullable=True),
        sa.Column(
            "n_gust_flagged_peak", sa.Integer(), nullable=False, server_default="0",
        ),
        sa.Column("sum_gust_flagged_over_peak_kt", sa.Float(), nullable=True),
        sa.Column(
            "n_model_gust_flag", sa.Integer(), nullable=False, server_default="0",
        ),
        sa.Column("n_obs_gust", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_gust_flag_hit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_advisory_match", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_advisory_opt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_advisory_pess", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_precip_hit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_precip_miss", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_precip_false_alarm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_convection_hit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_convection_miss", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_convection_false_alarm", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "date", "source", "model", "days_out", name="uq_vgds_key",
        ),
    )
    op.create_index(
        "ix_vgds_date", "verification_global_daily_stats", ["date"],
    )
    op.create_index(
        "ix_vgds_source_model_days_date",
        "verification_global_daily_stats",
        ["source", "model", "days_out", "date"],
    )

    op.create_table(
        "verification_activity_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("n_scores", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_distinct_obs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_airports_day", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("date", "source", name="uq_vad_key"),
    )
    op.create_index(
        "ix_vad_source_date", "verification_activity_daily", ["source", "date"],
    )

    op.create_table(
        "taf_verification_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("days_out", sa.Integer(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_ceiling", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_wind", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_vis", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cat_match", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cat_opt_1", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cat_opt_2", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cat_pess_1", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cat_pess_2", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sum_abs_ceiling_delta_ft", sa.Float(), nullable=True),
        sa.Column("sum_ceiling_delta_ft", sa.Float(), nullable=True),
        sa.Column("sum_abs_wind_delta_kt", sa.Float(), nullable=True),
        sa.Column("sum_abs_vis_delta_m", sa.Float(), nullable=True),
        sa.Column("n_advisory_match", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_advisory_opt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_advisory_pess", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_gust", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sum_abs_gust_delta_kt", sa.Float(), nullable=True),
        sa.Column("sum_gust_delta_kt", sa.Float(), nullable=True),
        sa.Column(
            "n_gust_flagged_peak", sa.Integer(), nullable=False, server_default="0",
        ),
        sa.Column("sum_gust_flagged_over_peak_kt", sa.Float(), nullable=True),
        sa.Column(
            "n_taf_gust_flag", sa.Integer(), nullable=False, server_default="0",
        ),
        sa.Column("n_obs_gust", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_gust_flag_hit", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("date", "source", "days_out", name="uq_tvd_key"),
    )
    op.create_index(
        "ix_tvd_source_days_date",
        "taf_verification_daily",
        ["source", "days_out", "date"],
    )

    op.create_table(
        "archive_manifest",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("table_name", sa.String(length=64), nullable=False),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column(
            "max_id", sa.BigInteger(), nullable=False, server_default="0",
        ),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("table_name", "period", name="uq_archive_manifest_key"),
    )
    op.create_index("ix_archive_manifest_table", "archive_manifest", ["table_name"])


def downgrade() -> None:
    op.drop_index("ix_archive_manifest_table", table_name="archive_manifest")
    op.drop_table("archive_manifest")

    op.drop_index("ix_tvd_source_days_date", table_name="taf_verification_daily")
    op.drop_table("taf_verification_daily")

    op.drop_index("ix_vad_source_date", table_name="verification_activity_daily")
    op.drop_table("verification_activity_daily")

    op.drop_index(
        "ix_vgds_source_model_days_date",
        table_name="verification_global_daily_stats",
    )
    op.drop_index("ix_vgds_date", table_name="verification_global_daily_stats")
    op.drop_table("verification_global_daily_stats")
