"""Registration evidence is independent of image-match diagnostics.

No real CTTH evidence is currently registered. Synthetic fixtures cannot authorize
production ground speed, projections or cross-source spatial relationships.
"""
from dataclasses import dataclass

import numpy as np

from weatherbrief.models.observed_motion import GeolocationRecord


@dataclass(frozen=True)
class RegistrationEvidence:
    evidence_id: str
    method_version: str
    product_id: str
    grid_id: str
    decoder_version: str
    domain_id: str
    synthetic: bool = False
    reviewed: bool = False
    checks_passed: bool = False


def registration_for(source_id, product_id, grid_id, decoder_version, domain_id, *, evidence=None):
    """Apply a reviewed, exact-applicability manifest; never accept synthetic."""
    if (evidence is not None and not evidence.synthetic and evidence.reviewed and evidence.checks_passed
            and (evidence.product_id,evidence.grid_id,evidence.decoder_version,evidence.domain_id)
            == (product_id,grid_id,decoder_version,domain_id)):
        return GeolocationRecord(status="validated",reason_codes=[],evidence_id=evidence.evidence_id,
                                 method_version=evidence.method_version,applicability_id=domain_id)
    return GeolocationRecord(status="unverified",reason_codes=["geolocation_unverified"],
                             evidence_id=None,method_version=None,applicability_id=None)


def radar_registration(source_grid, product_id, grid_id, decoder_version, domain_id):
    """Documented ground-grid structural/round-trip evidence, not forecast skill."""
    try:
        if (min(source_grid.nx,source_grid.ny)<=0 or
                not np.isfinite([source_grid.x0,source_grid.y0,source_grid.dx,source_grid.dy]).all()
                or not np.allclose([abs(source_grid.dx),abs(source_grid.dy)],2000,rtol=0,atol=1e-6)):
            raise ValueError("grid structure")
        cols=np.array([0,source_grid.nx-1,source_grid.nx/2])
        rows=np.array([0,source_grid.ny-1,source_grid.ny/2])
        lon,lat=source_grid.colrow_to_lonlat(cols,rows)
        back_col,back_row=source_grid.lonlat_to_colrow(lon,lat)
        if (not np.isfinite([lon,lat]).all() or np.any(np.abs(lon)>180) or np.any(np.abs(lat)>90)
                or not np.allclose(back_col,cols,atol=1e-5) or not np.allclose(back_row,rows,atol=1e-5)):
            raise ValueError("grid round trip")
    except (ValueError,TypeError):
        return GeolocationRecord(status="failed",reason_codes=["geolocation_failed"],
                                 evidence_id=None,method_version=None,applicability_id=None)
    return GeolocationRecord(status="validated",reason_codes=[],
                             evidence_id=f"odim_structural_v1:{grid_id}",method_version="odim_ground_structural_v1",
                             applicability_id=f"{product_id}:{decoder_version}:{domain_id}")
