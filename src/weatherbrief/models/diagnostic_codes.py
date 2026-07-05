"""Stable machine-readable codes for pipeline diagnostics.

Each stage owns its own ``StrEnum``. The string values are the persisted form
— never rename them, only add new ones. Telemetry, log filters, and any future
i18n will key off these values.
"""

from enum import StrEnum


class FetchCode(StrEnum):
    MODEL_FETCH_FAILED = "model_fetch_failed"
    MODEL_SKIPPED_REGION = "model_skipped_region"
    MODEL_SKIPPED_RANGE = "model_skipped_range"
    # The flight window lies beyond the provider's actual data_end_time
    # (live horizon), which can be shorter than the static config horizon
    # for a given run. Distinct from MODEL_SKIPPED_RANGE (static config).
    MODEL_SKIPPED_HORIZON = "model_skipped_horizon"
    # The fetch returned a structurally-valid response containing only
    # null values across all hourly slots — defensive trap for the same
    # condition when the horizon precheck couldn't catch it.
    MODEL_RETURNED_EMPTY = "model_returned_empty"
    GRIB_ENRICHMENT_FAILED = "grib_enrichment_failed"
    GRIB_ENRICHMENT_APPLIED = "grib_enrichment_applied"
    GRIB_SKIPPED_OUT_OF_RANGE = "grib_skipped_out_of_range"
    # Route lies outside the model's GRIB grid (e.g. ICON-EU covers only
    # Europe). The model itself may still be fetched via another source.
    GRIB_SKIPPED_OUT_OF_DOMAIN = "grib_skipped_out_of_domain"
    GRIB_UNAVAILABLE_FOR_MODEL = "grib_unavailable_for_model"


class DigestCode(StrEnum):
    ANTHROPIC_INTERNAL_ERROR = "anthropic_internal_error"
    ANTHROPIC_OVERLOADED = "anthropic_overloaded"
    ANTHROPIC_RATE_LIMITED = "anthropic_rate_limited"
    ANTHROPIC_TIMEOUT = "anthropic_timeout"
    ANTHROPIC_CONNECTION = "anthropic_connection"
    ANTHROPIC_AUTH_ERROR = "anthropic_auth_error"
    DIGEST_BAD_REQUEST = "digest_bad_request"
    DIGEST_UNKNOWN = "digest_unknown"


class GrametCode(StrEnum):
    GRAMET_NOT_AVAILABLE = "gramet_not_available"
    GRAMET_FETCH_FAILED = "gramet_fetch_failed"
    GRAMET_NO_OUTPUT_DIR = "gramet_no_output_dir"


class SkewtCode(StrEnum):
    SKEWT_METPY_NOT_AVAILABLE = "skewt_metpy_not_available"
    SKEWT_GENERATION_FAILED = "skewt_generation_failed"
    SKEWT_NO_OUTPUT_DIR = "skewt_no_output_dir"


class AdvisoryCode(StrEnum):
    ALT_ADVISORY_FAILED = "alt_advisory_failed"
