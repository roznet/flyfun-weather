"""Read a few chunks of a remote NetCDF4 (HDF5) file with HTTP range requests (#937).

The Met Office publishes UKV and the MO global model on AWS as one NetCDF4
file per parameter and step, 15-35 MB for a field on 33 levels, chunked
``(1, 128, 128)`` with plain gzip. A briefing needs only the handful of
128 x 128 tiles under its route, so downloading whole files would be ~99 %
waste (about 8 GB for one 54 h run).

h5py can open a file-like object, but it then reads the chunk index one
B-tree node at a time and holds its global lock while doing it: ~80 serial
range requests per 3D file, and no two files in parallel. So h5py is used
only for what it does cheaply from the file's head and tail (find the
variable, its dtype, chunk shape, filters, fill value and the coordinate
vectors), and this module walks the chunk B-tree itself, one tree level per
round of parallel requests, then fetches and inflates exactly the chunks
asked for.

Supported layout: HDF5 object header v2, data layout message v3 (chunked,
version-1 B-tree index), filters gzip only (no shuffle, no Fletcher32, no
scale-offset). That is what netCDF-C 4.9 writes for these files (checked on
UKV and global runs of 2026-10-25). Anything else falls back to h5py's own
``get_chunk_info_by_coord``: slower, same answer. A filter other than plain
gzip raises :class:`UnsupportedLayout`.

Chunk keys are element offsets of the chunk's first element, e.g.
``(level_index, 384, 512)`` for a ``(1, 128, 128)`` chunk on a 3D variable.
"""

from __future__ import annotations

import io
import logging
import struct
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
import requests

from weatherbrief.fetch.grib.http_retry import get_with_retry

logger = logging.getLogger(__name__)

# The file head holds the superblock and the object headers; the last tens of
# KB hold the root group's dense link storage. Both are fetched up front so
# most of h5py's metadata reads are cache hits; the few that are not (a 3D
# file's attribute heap, ~250 KB before the end) cost one small block each.
# Measured on UKV files of 2026-10-25: 11-13 metadata reads per file, all
# within 16 KB of the start or 260 KB of the end.
_HEAD_BYTES = 16 * 1024
_TAIL_BYTES = 48 * 1024
# Block size for metadata reads that miss head and tail.
_BLOCK_BYTES = 4 * 1024
# Two chunk ranges closer than this are fetched as one request.
_MERGE_GAP_BYTES = 8 * 1024
# A v1 B-tree node for chunked data with the default K=32 is 3,136 bytes on a
# 3D variable (2,616 on 2D); a node that holds more is re-read whole.
_NODE_READ_BYTES = 3_200
_TIMEOUT_S = 30.0

# Variables that are never the payload of a Met Office field file.
_NON_PAYLOAD_NAMES = frozenset({
    "flag", "bnds", "time", "forecast_period", "forecast_reference_time",
    "height", "pressure", "lambert_azimuthal_equal_area", "latitude_longitude",
    "projection_x_coordinate", "projection_y_coordinate", "latitude", "longitude",
})


class UnsupportedLayout(RuntimeError):
    """The file uses a storage feature this reader does not decode."""


class RemoteFileMissing(RuntimeError):
    """The object is not in the bucket (HTTP 403/404 from S3)."""


@dataclass
class TransferStats:
    """Requests and bytes one briefing's reads cost, for the log line (#937)."""

    requests: int = 0
    bytes: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, n_bytes: int) -> None:
        with self._lock:
            self.requests += 1
            self.bytes += n_bytes


def _get_range(
    session: requests.Session, url: str, lo: int, hi: int, stats: TransferStats | None,
) -> requests.Response:
    """GET ``bytes=lo-hi`` (inclusive), retried on transient failures."""
    resp = get_with_retry(
        session, url, timeout=_TIMEOUT_S, headers={"Range": f"bytes={lo}-{hi}"},
    )
    if resp.status_code in (403, 404):
        # S3 answers 403 for a missing key on a bucket without ListBucket for
        # the caller; this bucket allows listing, but treat both the same.
        raise RemoteFileMissing(url)
    resp.raise_for_status()
    if stats is not None:
        stats.add(len(resp.content))
    return resp


class _RangeFile(io.RawIOBase):
    """Seekable read-only file over HTTP ranges, with a block cache.

    Head and tail are fetched in parallel on construction. Only h5py reads
    through the file interface; the B-tree walk uses :meth:`read_at`.
    """

    def __init__(
        self, session: requests.Session, url: str, stats: TransferStats | None,
    ):
        super().__init__()
        self._session = session
        self.url = url
        self._stats = stats
        self._pos = 0
        self._blocks: dict[int, bytes] = {}
        self._lock = threading.Lock()
        head = _get_range(session, url, 0, _HEAD_BYTES - 1, stats)
        content_range = head.headers.get("Content-Range", "")
        try:
            self.size = int(content_range.rsplit("/", 1)[-1])
        except ValueError:
            self.size = len(head.content)
        self._head = head.content
        self._tail_start = max(len(self._head), self.size - _TAIL_BYTES)
        self._tail = b""
        if self._tail_start < self.size:
            self._tail = _get_range(
                session, url, self._tail_start, self.size - 1, stats,
            ).content

    # --- io.RawIOBase ---------------------------------------------------
    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        else:
            self._pos = self.size + offset
        return self._pos

    def readinto(self, buffer) -> int:
        data = self.read_at(self._pos, len(buffer))
        buffer[: len(data)] = data
        self._pos += len(data)
        return len(data)

    # --- random access --------------------------------------------------
    def read_at(self, offset: int, n: int) -> bytes:
        """``n`` bytes at ``offset`` (fewer at end of file)."""
        end = min(self.size, offset + n)
        if offset >= end:
            return b""
        if end <= len(self._head):
            return self._head[offset:end]
        if offset >= self._tail_start and self._tail:
            return self._tail[offset - self._tail_start: end - self._tail_start]
        out = bytearray()
        block = _BLOCK_BYTES
        for b in range(offset // block, (end - 1) // block + 1):
            out += self._block(b)
        start = offset - (offset // block) * block
        return bytes(out[start: start + (end - offset)])

    def _block(self, b: int) -> bytes:
        with self._lock:
            cached = self._blocks.get(b)
        if cached is not None:
            return cached
        lo = b * _BLOCK_BYTES
        hi = min(self.size, lo + _BLOCK_BYTES) - 1
        data = _get_range(self._session, self.url, lo, hi, self._stats).content
        with self._lock:
            self._blocks[b] = data
        return data


@dataclass
class ChunkedVariable:
    """What a range read needs to know about one chunked variable."""

    url: str
    name: str
    shape: tuple[int, ...]
    chunks: tuple[int, ...]
    dtype: np.dtype
    fill_value: float | None
    attrs: dict[str, object]
    coords: dict[str, np.ndarray]


def _payload_name(names: list[str], wanted: str | None) -> str:
    if wanted is not None:
        if wanted not in names:
            raise UnsupportedLayout(f"variable {wanted!r} not in file ({names})")
        return wanted
    candidates = [
        n for n in names
        if n not in _NON_PAYLOAD_NAMES and not n.endswith("_bnds")
    ]
    if len(candidates) != 1:
        raise UnsupportedLayout(f"cannot tell the payload variable from {names}")
    return candidates[0]


def _attr_value(v: object) -> object:
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, np.ndarray) and v.size == 1:
        return v.reshape(-1)[0].item()
    if isinstance(v, np.generic):
        return v.item()
    return v


# --- HDF5 structures ------------------------------------------------------

_OHDR_LAYOUT_MSG = 0x0008
_OHDR_CONTINUATION_MSG = 0x0010


def _layout_btree_address(rf: _RangeFile, ohdr_addr: int) -> tuple[int, int]:
    """``(btree_root_address, dimensionality)`` from a v2 object header.

    Walks the header's messages (following continuation blocks) to the data
    layout message. Raises :class:`UnsupportedLayout` for anything but a v3
    chunked layout, whose index is a version-1 B-tree.
    """
    head = rf.read_at(ohdr_addr, 64)
    if head[:4] != b"OHDR" or head[4] != 2:
        raise UnsupportedLayout("object header is not version 2")
    flags = head[5]
    p = 6
    if flags & 0x20:
        p += 16  # access/modification/change/birth times
    if flags & 0x10:
        p += 4  # max compact / min dense attribute counts
    size_len = 1 << (flags & 0x03)
    chunk0_size = int.from_bytes(head[p:p + size_len], "little")
    p += size_len
    blocks = [(ohdr_addr + p, chunk0_size)]
    track_order = bool(flags & 0x04)
    seen = 0
    while blocks:
        start, length = blocks.pop(0)
        data = rf.read_at(start, length)
        q = 0
        # The last 4 bytes of a block are its checksum; a gap smaller than a
        # message header may precede them.
        while q + 4 <= len(data) - 4:
            mtype = data[q]
            msize = int.from_bytes(data[q + 1:q + 3], "little")
            q += 4 + (2 if track_order else 0)
            body = data[q:q + msize]
            q += msize
            seen += 1
            if mtype == _OHDR_CONTINUATION_MSG:
                addr, clen = struct.unpack_from("<QQ", body, 0)
                # A continuation block starts with "OCHK" and ends with a checksum.
                blocks.append((addr + 4, clen - 4))
            elif mtype == _OHDR_LAYOUT_MSG:
                version, layout_class = body[0], body[1]
                if version != 3 or layout_class != 2:
                    raise UnsupportedLayout(
                        f"layout message v{version} class {layout_class}",
                    )
                ndims = body[2]
                (btree,) = struct.unpack_from("<Q", body, 3)
                return btree, ndims
        if seen > 256:
            break
    raise UnsupportedLayout("no data layout message")


@dataclass(frozen=True)
class _ChunkRef:
    address: int
    size: int
    filter_mask: int


def _read_node(
    rf: _RangeFile, addr: int, ndims: int,
) -> tuple[int, list[tuple[tuple[int, ...], int, int, int]], tuple[int, ...]]:
    """Parse a v1 B-tree node for chunked raw data (node type 1).

    Returns ``(level, entries, last_key)`` where each entry is
    ``(key_offsets, size, filter_mask, child_address)``: the child holds
    chunks from ``key_offsets`` up to the next entry's key (``last_key`` for
    the last one). ``ndims`` counts the trailing element-size dimension.
    """
    key_len = 8 + 8 * ndims
    data = rf.read_at(addr, _NODE_READ_BYTES)
    if data[:4] != b"TREE" or data[4] != 1:
        raise UnsupportedLayout("chunk index node is not a v1 raw-data B-tree")
    level = data[5]
    used = int.from_bytes(data[6:8], "little")
    need = 24 + used * (key_len + 8) + key_len
    if need > len(data):
        data = rf.read_at(addr, need)
    p = 24
    entries = []
    for _ in range(used):
        size, mask = struct.unpack_from("<II", data, p)
        offsets = struct.unpack_from(f"<{ndims}Q", data, p + 8)
        p += key_len
        (child,) = struct.unpack_from("<Q", data, p)
        p += 8
        entries.append((offsets[:-1], size, mask, child))
    last = struct.unpack_from(f"<{ndims}Q", data, p + 8)[:-1]
    return level, entries, last


def _walk_btree(
    rf: _RangeFile, root: int, ndims: int, wanted: set[tuple[int, ...]],
    pool: ThreadPoolExecutor,
) -> dict[tuple[int, ...], _ChunkRef]:
    """Find the wanted chunks, one tree level per parallel round of reads."""
    found: dict[tuple[int, ...], _ChunkRef] = {}
    frontier = {root: set(wanted)}
    for _ in range(16):  # tree depth guard
        if not frontier:
            break
        addrs = list(frontier)
        nodes = list(pool.map(lambda a: _read_node(rf, a, ndims), addrs))
        nxt: dict[int, set[tuple[int, ...]]] = {}
        for addr, (level, entries, last) in zip(addrs, nodes):
            keys = frontier[addr]
            if level == 0:
                for offsets, size, mask, child in entries:
                    if offsets in keys:
                        found[offsets] = _ChunkRef(child, size, mask)
                continue
            bounds = [e[0] for e in entries[1:]] + [last]
            for (lo, _s, _m, child), hi in zip(entries, bounds):
                inside = {k for k in keys if lo <= k < hi}
                if inside:
                    nxt.setdefault(child, set()).update(inside)
        frontier = nxt
    return found


def _merge_ranges(refs: list[tuple[tuple[int, ...], _ChunkRef]]):
    """Group chunk refs into contiguous-ish byte ranges, in file order."""
    groups: list[list[tuple[tuple[int, ...], _ChunkRef]]] = []
    for key, ref in sorted(refs, key=lambda kr: kr[1].address):
        if groups:
            last = groups[-1][-1][1]
            if ref.address - (last.address + last.size) <= _MERGE_GAP_BYTES:
                groups[-1].append((key, ref))
                continue
        groups.append([(key, ref)])
    return groups


def read_chunks(
    session: requests.Session,
    url: str,
    keys: set[tuple[int, ...]],
    *,
    variable: str | None = None,
    coord_names: tuple[str, ...] = (),
    pool: ThreadPoolExecutor,
    stats: TransferStats | None = None,
) -> tuple[ChunkedVariable, dict[tuple[int, ...], np.ndarray]]:
    """Fetch and inflate the chunks of one variable that start at ``keys``.

    Returns the variable description and ``{key: array}``, each array shaped
    like a chunk (edge chunks keep their full stored size; the caller clips).
    A key not present in the file's index is absent from the result. Values
    equal to the fill value are NaN.

    Raises :class:`RemoteFileMissing` when the object does not exist.
    """
    import h5py

    rf = _RangeFile(session, url, stats)
    f = h5py.File(rf, "r")
    try:
        names = list(f.keys())
        name = _payload_name(names, variable)
        ds = f[name]
        var = ChunkedVariable(
            url=url, name=name, shape=tuple(ds.shape), chunks=tuple(ds.chunks or ()),
            dtype=ds.dtype, fill_value=_attr_value(ds.fillvalue),
            attrs={k: _attr_value(v) for k, v in ds.attrs.items()
                   if k not in ("DIMENSION_LIST", "REFERENCE_LIST")},
            coords={c: np.asarray(f[c][...]) for c in coord_names if c in names},
        )
        if not var.chunks:
            raise UnsupportedLayout(f"{name} is not chunked")
        if ds.compression not in (None, "gzip") or ds.shuffle or ds.fletcher32 \
                or ds.scaleoffset is not None:
            raise UnsupportedLayout(f"{name}: filters other than plain gzip")
        compressed = ds.compression == "gzip"
        try:
            # links.get_info is a cheap link lookup; h5o.get_info would walk
            # the whole chunk index to report its metadata size.
            addr = f.id.links.get_info(name.encode()).u
            root, ndims = _layout_btree_address(rf, addr)
            refs = _walk_btree(rf, root, ndims, set(keys), pool)
        except UnsupportedLayout as exc:
            logger.info("nc_range: %s, using h5py's chunk lookup (%s)", url.rsplit("/", 1)[-1], exc)
            refs = {}
            for k in keys:
                info = ds.id.get_chunk_info_by_coord(k)
                if info.byte_offset is not None and info.size:
                    refs[k] = _ChunkRef(info.byte_offset, info.size, info.filter_mask)
    finally:
        f.close()

    out: dict[tuple[int, ...], np.ndarray] = {}
    groups = _merge_ranges(list(refs.items()))

    def _fetch(group):
        lo = group[0][1].address
        hi = max(r.address + r.size for _, r in group) - 1
        return lo, _get_range(session, url, lo, hi, stats).content

    fill = var.fill_value
    for group, (lo, blob) in zip(groups, pool.map(_fetch, groups)):
        for key, ref in group:
            raw = blob[ref.address - lo: ref.address - lo + ref.size]
            if compressed and not (ref.filter_mask & 1):
                raw = zlib.decompress(raw)
            arr = np.frombuffer(raw, dtype=var.dtype).reshape(var.chunks)
            arr = arr.astype(np.float32, copy=True)
            if fill is not None:
                arr[arr == np.float32(fill)] = np.nan
            out[key] = arr
    return var, out
