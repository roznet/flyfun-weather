"""Cutoff-safe, content-pinned local inputs for experimental motion."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from weatherbrief.models.observed_motion import FrameGap, FrameRecord, Interval, SourceRecord, SupportRecord, GeolocationRecord
from weatherbrief.observed import ctth, lightning, opera
from weatherbrief.observed.frames import FrameStore, SOURCE_SPECS, StoredFrame, FlashFrame, aware_time
from weatherbrief.observed.grid import GridSpec, compute_window
from .geometry import AnalysisGrid, build_analysis_grid, decode_ctth, radar_window, sample_radar
from .policy import DEFAULT_POLICY
from .validation import radar_registration, registration_for


def check_deadline(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise ValueError("compute_deadline")


def content_id(path: Path, deadline=None):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            check_deadline(deadline)
            digest.update(block)
    return digest.hexdigest()


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@dataclass(frozen=True)
class PinnedFrame:
    stored: StoredFrame
    record: FrameRecord
    sidecar_content_id: str
    metadata: dict

    @property
    def received_at(self):
        return self.record.received_at

    @property
    def reference_at(self):
        return self.record.reference_at

    @property
    def frame_id(self):
        return self.record.frame_id

    def recheck(self, deadline=None):
        try:
            return (content_id(self.stored.path.with_suffix(".json"), deadline) == self.sidecar_content_id
                    and content_id(self.stored.path, deadline) == self.record.content_id)
        except OSError:
            return False


@dataclass(frozen=True)
class Selection:
    frames: tuple[PinnedFrame, ...]
    gaps: tuple[FrameGap, ...]
    reason_codes: tuple[str, ...]
    inventory_count: int | None = None
    inspected_count: int = 0
    selection_complete: bool = False


def _metadata(stored):
    if stored.source.startswith("opera_"):
        return opera.read_metadata(stored.path, SOURCE_SPECS[stored.source].quantity)
    if stored.source == "eumetsat_ctth":
        return ctth.read_metadata(stored.path)
    return lightning.read_metadata(stored.path)


def _pin(stored, cutoff, deadline):
    if stored.meta.get("source") != stored.source:
        raise ValueError("frame_changed")
    sidecar_path = stored.path.with_suffix(".json")
    sidecar_id = content_id(sidecar_path, deadline)
    if json.loads(sidecar_path.read_text()) != stored.meta:
        raise ValueError("frame_changed")
    payload_id = content_id(stored.path, deadline)
    meta = _metadata(stored)
    if meta.get("acquisition_start") is None or meta.get("acquisition_end") is None:
        raise ValueError("missing_acquisition")
    try:
        start, end = aware_time(meta["acquisition_start"]), aware_time(meta["acquisition_end"])
        time_key = "motion_valid_time" if stored.source.startswith("opera_") else "valid_time"
        valid, receipt = aware_time(meta[time_key]), aware_time(stored.meta["received_at"])
    except (TypeError, ValueError):
        raise ValueError("invalid_time") from None
    if end > cutoff:
        raise ValueError("future_acquisition")
    if not start <= end <= receipt <= cutoff or valid > receipt:
        raise ValueError("invalid_time")
    # Canonical target is independent of acquisition end and filename slot.
    # Old radar sidecars without the explicit target are inventory barriers.
    if valid != stored.valid_time:
        raise ValueError("frame_changed")
    for key in ("grid", "product_id", "decoder_version", "acquisition_start", "acquisition_end"):
        if key in stored.meta and stored.meta[key] != meta.get(key):
            raise ValueError("frame_changed")
    grid_id = identity(meta.get("grid", {"kind": "point_detections"}))
    product = meta["product_id"]
    decoder = meta["decoder_version"]
    record = FrameRecord(frame_id=identity([stored.source, payload_id, sidecar_id, valid]),
                         content_id=payload_id, product_id=product, decoder_version=decoder,
                         grid_id=grid_id, valid_at=valid, received_at=receipt,
                         reference_at=valid, acquisition_window=Interval(start_at=start, end_at=end))
    pinned = PinnedFrame(stored, record, sidecar_id, meta)
    if not pinned.recheck(deadline):
        raise ValueError("frame_changed")
    return pinned


def select_history(store: FrameStore, source_id: str, cutoff_at: datetime, policy=DEFAULT_POLICY, *, deadline=None, observed_intervals=()):
    """Newest usable chronological suffix; known bad inputs are hard barriers."""
    cutoff_at = aware_time(cutoff_at)
    entries = store.as_of_inventory(source_id, cutoff_at)
    frames, gaps, reasons = [], [], []
    inspected = 0
    selection_complete = True
    primary = source_id in ("opera_dbzh", "eumetsat_ctth")
    cadence = SOURCE_SPECS[source_id].interval.total_seconds()
    max_gap = (policy.max_dbzh_adjacent_gap_minutes if source_id == "opera_dbzh"
               else policy.max_ctth_adjacent_gap_minutes) * 60
    for entry in entries:
        check_deadline(deadline)
        if primary and len(frames) >= policy.max_primary_frames_per_source:
            break
        if entry.reason_codes:
            reasons.extend(entry.reason_codes)
            selection_complete = False
            break
        try:
            inspected += 1
            pinned = _pin(entry.stored, cutoff_at, deadline)
        except ValueError as exc:
            reason = str(exc)
            reasons.append(reason if reason in ("missing_acquisition", "invalid_time", "future_acquisition", "frame_changed", "compute_deadline") else "unreadable_frame")
            selection_complete = False
            break
        except (OSError, KeyError, RuntimeError):
            reasons.append("unreadable_frame")
            selection_complete = False
            break
        if not primary and observed_intervals:
            window=pinned.record.acquisition_window
            eligible=(any(start<=pinned.reference_at<=end for start,end in observed_intervals) if source_id=="opera_rate"
                      else any(window.start_at<=end and window.end_at>=start for start,end in observed_intervals))
            if not eligible:
                continue
        if not primary and len(frames)>=policy.max_primary_frames_per_source:
            reasons.append("selection_limit")
            break
        if frames and primary:
            newer = frames[-1].record
            older = pinned.record
            elapsed = (newer.valid_at-older.valid_at).total_seconds()
            if elapsed <= 0:
                reasons.append("invalid_time")
                break
            if elapsed > max_gap or (frames[0].reference_at-pinned.reference_at).total_seconds() > policy.max_history_span_minutes * 60:
                reasons.append("history_gap")
                break
            if older.grid_id != newer.grid_id:
                reasons.append("incompatible_grid")
                break
            if (older.product_id, older.decoder_version) != (newer.product_id, newer.decoder_version):
                reasons.append("incompatible_product")
                break
            missing = max(0, round(elapsed/cadence)-1)
            if missing:
                gaps.append(FrameGap(from_frame_id=older.frame_id, to_frame_id=newer.frame_id,
                                     elapsed_seconds=elapsed, missing_nominal_publications=missing,
                                     reason_codes=["history_gap"]))
        frames.append(pinned)
    if not entries:
        reasons.append("missing_source")
    if primary and len(frames) < policy.min_primary_valid_times:
        reasons.append("insufficient_history")
    if frames and (cutoff_at-frames[0].reference_at).total_seconds() > policy.max_reference_age_minutes*60:
        reasons.append("stale_reference")
    return Selection(tuple(reversed(frames)), tuple(reversed(gaps)), tuple(dict.fromkeys(reasons)),
                     len(entries), inspected, selection_complete)


@dataclass
class AnalysisFrame:
    source_id: str
    frame_id: str
    reference_at: datetime
    grid: AnalysisGrid
    descriptor: np.ndarray
    known: np.ndarray
    detected: np.ndarray
    values: np.ndarray
    temperature_k: np.ndarray | None
    source_record: FrameRecord
    geolocation: GeolocationRecord
    sample_ids: np.ndarray | None = None
    sample_positions: np.ndarray | None = None
    quality: np.ndarray | None = None
    collision_count: int = 0


@dataclass(frozen=True)
class LightningInput:
    source_record: FrameRecord
    frame: FlashFrame


@dataclass(frozen=True)
class InputCount:
    """Counts of as-of inventory candidates, not detected weather or coverage.

    Known barriers count as inventory entries. Future valid/receipt entries do
    not. Omitted includes outside-history, capped, invalid and undecoded entries.
    ``selection_complete`` means bounded selection finished without unreadable
    input/deadline; it does not imply every retained file was decoded.
    """
    source_id: str
    considered_count: int | None
    inspected_count: int
    selected_count: int
    emitted_count: int
    omitted_count: int | None
    selection_complete: bool
    reason_codes: tuple[str, ...] = ()


@dataclass
class HistoryResult:
    grid: AnalysisGrid | None
    frames_by_source: dict[str, tuple[AnalysisFrame, ...]]
    sources: tuple[SourceRecord, ...]
    reason_codes: tuple[str, ...]
    rate_frames: tuple[AnalysisFrame, ...] = ()
    lightning_frames: tuple[LightningInput, ...] = ()
    input_counts: tuple[InputCount, ...] = ()


def _input_count(source, selected, emitted=0, reasons=()):
    return InputCount(source,selected.inventory_count,selected.inspected_count,len(selected.frames),emitted,
                      selected.inventory_count-emitted if selected.inventory_count is not None else None,
                      selected.selection_complete and not reasons,tuple(dict.fromkeys([*selected.reason_codes,*reasons])))


def _decode(pinned, grid, policy, deadline):
    source=pinned.stored.source
    source_grid=GridSpec(**pinned.metadata["grid"])
    domain_id=identity(grid.to_record().model_dump(mode="json"))
    if source.startswith("opera_"):
        window=radar_window(source_grid,grid,policy)
        frame=opera.read_window(pinned.stored.path,SOURCE_SPECS[source].quantity,window,
                                source=source,units=SOURCE_SPECS[source].units)
        samples=sample_radar(frame,grid,policy)
        geolocation=radar_registration(source_grid,pinned.record.product_id,pinned.record.grid_id,
                                       pinned.record.decoder_version,domain_id)
    else:
        lon,lat=grid.boundary_lonlat()
        window=compute_window(source_grid,lat,lon,radius_km=0,
                              pad_km=ctth.parallax_pad_km(lat,lon),full_width=True)
        samples=decode_ctth(pinned.stored.path,grid,window,policy,deadline=deadline)
        geolocation=registration_for(source,pinned.record.product_id,pinned.record.grid_id,
                                     pinned.record.decoder_version,domain_id)
    if not pinned.recheck(deadline):
        raise ValueError("frame_changed")
    return AnalysisFrame(source,pinned.frame_id,pinned.reference_at,grid,samples.descriptor,samples.known,
                         samples.detected,samples.values,samples.temperature_k if source=="eumetsat_ctth" else None,
                         pinned.record,geolocation,samples.sample_ids,samples.sample_positions,samples.quality,samples.collisions)


def _source_record(source, selected, loaded, extra_reasons=(), *, point=False):
    reasons=list(dict.fromkeys([*selected.reason_codes,*extra_reasons]))
    unverified=registration_for(source,"unknown","unknown","unknown","unknown")
    if loaded:
        if point:
            records=[item.source_record for item in loaded]
            coverage=SupportRecord(status="unavailable",reason_codes=["point_coverage_unknown"],scope="point_detections",
                                   known_cells=None,total_cells=None,known_fraction=None)
            geolocation=unverified
        else:
            records=[item.source_record for item in loaded]
            known=int(loaded[-1].known.sum()); total=loaded[-1].known.size
            coverage=SupportRecord(status="available" if total else "unavailable",reason_codes=[] if total else ["unknown_support"],
                                   scope="analysis_domain",known_cells=known,total_cells=total,
                                   known_fraction=known/total if total else None)
            geolocation=loaded[-1].geolocation
        status="available"
    else:
        records=[]; status="unavailable"
        if not reasons:
            reasons=["missing_source"]
        coverage=SupportRecord(status="unavailable",reason_codes=["point_coverage_unknown" if point else "unknown_support"],
                               scope="point_detections" if point else "analysis_domain",known_cells=None,total_cells=None,known_fraction=None)
        geolocation=unverified
    emitted={r.frame_id for r in records}
    gaps=[gap for gap in selected.gaps if gap.from_frame_id in emitted and gap.to_frame_id in emitted]
    attribution=next((p.metadata.get("attribution",{}).get("text","") for p in reversed(selected.frames)),"")
    return SourceRecord(source_id=source,status=status,reason_codes=reasons,frames=records,gaps=gaps,
                        attribution=attribution,coverage=coverage,geolocation=geolocation)


def load_history(store, route, cutoff_at, policy=DEFAULT_POLICY, *, deadline=None):
    """Load bounded primary histories, then eligible observed-only RATE/LI context.

    No provider calls or shared observation changes. A failed older decode cuts
    the history suffix; a failed newest decode does not expose an older chain as
    if it ended at the requested current observation.
    """
    cutoff_at=aware_time(cutoff_at)
    if deadline is None:
        deadline=time.monotonic()+policy.compute_budget_seconds
    selections={}; by_source={}; source_records=[]; rates=[]; flashes=[]; reasons=[]; counts=[]
    try:
        check_deadline(deadline)
        for source in ("opera_dbzh","eumetsat_ctth"):
            selections[source]=select_history(store,source,cutoff_at,policy,deadline=deadline)
        spans=[(s.frames[-1].reference_at-s.frames[0].reference_at).total_seconds() for s in selections.values() if s.frames]
        grid=build_analysis_grid(route,max(spans,default=0),policy)
    except ValueError as exc:
        counts=tuple(_input_count(source,selections.get(source,Selection((),(),())),reasons=(str(exc),))
                     for source in ("opera_dbzh","eumetsat_ctth","opera_rate","eumetsat_li"))
        return HistoryResult(None,{},(),(str(exc),),input_counts=counts)
    for source in ("opera_dbzh","eumetsat_ctth"):
        selected=selections[source]; loaded=[]; errors=[]
        for pinned in reversed(selected.frames):
            try:
                check_deadline(deadline)
                loaded.append(_decode(pinned,grid,policy,deadline))
            except (ValueError,OSError,KeyError,RuntimeError) as exc:
                code=str(exc)
                errors.append(code if code in ("compute_deadline","frame_changed","source_window_limit","unsupported_grid_spacing") else "unreadable_frame")
                break
        loaded.reverse()
        if len(loaded)<policy.min_primary_valid_times:
            errors.append("insufficient_history")
        by_source[source]=tuple(loaded)
        source_records.append(_source_record(source,selected,loaded,errors))
        counts.append(_input_count(source,selected,len(loaded),errors))
        reasons.extend([*selected.reason_codes,*errors])
    intervals=[(frames[0].reference_at,frames[-1].reference_at) for frames in by_source.values() if frames]
    radar=by_source["opera_dbzh"]
    for source in ("opera_rate","eumetsat_li"):
        errors=[]; loaded=[]
        selected=Selection((),(),())
        if not intervals:
            selected=Selection((),(),("no_common_history",))
        else:
            try:
                context_intervals=((radar[0].reference_at,radar[-1].reference_at),) if source=="opera_rate" and radar else intervals
                selected=select_history(store,source,cutoff_at,policy,deadline=deadline,observed_intervals=context_intervals)
                for pinned in selected.frames:
                    check_deadline(deadline)
                    if source=="opera_rate":
                        if radar and radar[0].reference_at<=pinned.reference_at<=radar[-1].reference_at:
                            loaded.append(_decode(pinned,grid,policy,deadline))
                    else:
                        window=pinned.record.acquisition_window
                        if any(window.start_at<=end and window.end_at>=start for start,end in intervals):
                            frame=lightning.read_flashes(pinned.stored.path,source=source,window_minutes=SOURCE_SPECS[source].window_minutes)
                            # Regional retention is bounded by the same analysis domain.
                            x,y=grid.project(frame.lons,frame.lats)
                            keep=(x>=grid.origin_x_m)&(x<=grid.domain.bounds[2])&(y>=grid.origin_y_m)&(y<=grid.domain.bounds[3])
                            indices=np.flatnonzero(keep)
                            frame.lons,frame.lats,frame.times=frame.lons[keep],frame.lats[keep],frame.times[keep]
                            frame.time_precision=tuple(frame.time_precision[i] for i in indices)
                            frame.time_reason_codes=tuple(frame.time_reason_codes[i] for i in indices)
                            frame.event_times=tuple(frame.event_times[i] for i in indices)
                            frame.sample_ids=tuple(frame.sample_ids[i] for i in indices)
                            if not pinned.recheck(deadline):
                                raise ValueError("frame_changed")
                            loaded.append(LightningInput(pinned.record,frame))
            except (ValueError,OSError,KeyError,RuntimeError) as exc:
                code=str(exc)
                errors.append(code if code in ("compute_deadline","frame_changed","source_window_limit","unsupported_grid_spacing") else "unreadable_frame")
        source_records.append(_source_record(source,selected,loaded,errors,point=source=="eumetsat_li"))
        counts.append(_input_count(source,selected,len(loaded),errors))
        if source=="opera_rate":
            rates=loaded
        else:
            flashes=loaded
        reasons.extend([*selected.reason_codes,*errors])
    return HistoryResult(grid,by_source,tuple(source_records),tuple(dict.fromkeys(reasons)),tuple(rates),tuple(flashes),tuple(counts))
