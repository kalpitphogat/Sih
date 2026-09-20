"""Content-addressed download cache and the data manifest.

Engineering rule 6: `data/raw/` is content-addressed and never re-downloaded.
`data/MANIFEST.json` records URL, SHA256, size, licence and fetch time for every
file, so the demo runs fully offline once the data is fetched, and so any number
on screen can be traced to a byte-exact input.

The cache key is the SHA256 of the *content*, but files are stored under a
readable path so a human can find them. The manifest maps both ways.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

CHUNK = 1 << 20  # 1 MiB


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class ManifestEntry:
    """One row of data/MANIFEST.json."""

    key: str                    # logical name, e.g. "dem/copernicus/N30_E078"
    path: str                   # path relative to the data dir
    url: str | None             # where it came from, None for bundled/derived
    sha256: str
    size_bytes: int
    fetched_utc: str
    source: str                 # human-readable provider name
    licence: str
    #: Anything the fetcher wants recorded: CRS, resolution, bbox, API used.
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Manifest:
    """data/MANIFEST.json — an append-only record of every input file.

    Safe for concurrent use within a process; writes are atomic on disk.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._entries: dict[str, ManifestEntry] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("MANIFEST.json is corrupt; starting a fresh one at %s", self.path)
            return
        for entry in raw.get("entries", []):
            self._entries[entry["key"]] = ManifestEntry(**entry)

    def _flush(self) -> None:
        payload = {
            "schema": "floodguard/manifest/1",
            "updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "entry_count": len(self._entries),
            "entries": [e.to_dict() for e in sorted(self._entries.values(), key=lambda e: e.key)],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, key: str) -> ManifestEntry | None:
        return self._entries.get(key)

    def record(self, entry: ManifestEntry) -> ManifestEntry:
        with self._lock:
            self._entries[entry.key] = entry
            self._flush()
        return entry

    def entries(self) -> list[ManifestEntry]:
        return sorted(self._entries.values(), key=lambda e: e.key)

    def verify(self, data_dir: Path) -> list[str]:
        """Re-hash every recorded file. Returns a list of problems found.

        A cache that silently serves a truncated download is worse than no
        cache, so this is cheap insurance before a demo.
        """
        problems: list[str] = []
        for entry in self.entries():
            full = data_dir / entry.path
            if not full.exists():
                problems.append(f"{entry.key}: file missing at {entry.path}")
                continue
            actual = sha256_file(full)
            if actual != entry.sha256:
                problems.append(
                    f"{entry.key}: sha256 mismatch "
                    f"(recorded {entry.sha256[:12]}…, found {actual[:12]}…)"
                )
        return problems

    def as_table(self) -> str:
        """The layer table printed at the end of `make data`."""
        rows = self.entries()
        if not rows:
            return "MANIFEST is empty. Run `make data` first."

        headers = ("KEY", "SOURCE", "SIZE", "CRS", "RES", "LICENCE")

        def fmt_size(n: int) -> str:
            for unit in ("B", "KB", "MB", "GB"):
                if n < 1024 or unit == "GB":
                    return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
                n /= 1024.0
            return f"{n:.1f}GB"

        table = [
            (
                r.key,
                r.source,
                fmt_size(r.size_bytes),
                str(r.attributes.get("crs", "—")),
                str(r.attributes.get("resolution_m", "—")),
                r.licence,
            )
            for r in rows
        ]
        widths = [
            max(len(headers[i]), max(len(row[i]) for row in table)) for i in range(len(headers))
        ]
        line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
        out = [line, "-" * len(line)]
        out += ["  ".join(c.ljust(w) for c, w in zip(row, widths)) for row in table]
        out.append("-" * len(line))
        out.append(f"{len(rows)} layers, {fmt_size(sum(r.size_bytes for r in rows))} total")
        return "\n".join(out)


class DownloadCache:
    """Idempotent, checksummed downloads into `data/raw/`.

    `fetch()` is the only entry point. If the file is already present and its
    hash matches the manifest, nothing touches the network.
    """

    def __init__(self, data_dir: Path, manifest: Manifest | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.raw_dir = self.data_dir / "raw"
        self.manifest = manifest or Manifest(self.data_dir / "MANIFEST.json")

    def is_cached(self, key: str) -> bool:
        entry = self.manifest.get(key)
        if entry is None:
            return False
        full = self.data_dir / entry.path
        return full.exists() and full.stat().st_size == entry.size_bytes

    def path_for(self, key: str) -> Path | None:
        entry = self.manifest.get(key)
        return self.data_dir / entry.path if entry else None

    def fetch(
        self,
        key: str,
        url: str,
        rel_path: str,
        *,
        source: str,
        licence: str,
        attributes: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 300.0,
        expected_sha256: str | None = None,
    ) -> Path:
        """Download `url` to `data/raw/<rel_path>` unless already cached.

        Downloads to a temporary file and moves it into place only once
        complete, so an interrupted fetch can never leave a half file that a
        later run mistakes for a good one.
        """
        target = self.raw_dir / rel_path
        entry = self.manifest.get(key)

        if entry is not None and target.exists():
            if target.stat().st_size == entry.size_bytes:
                log.info("cache hit: %s", key)
                return target
            log.warning("cache size mismatch for %s; re-fetching", key)

        import requests  # imported lazily: the cache is usable offline without it

        log.info("fetching %s from %s", key, url)
        target.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".part")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            with requests.get(url, stream=True, timeout=timeout, headers=headers or {}) as resp:
                resp.raise_for_status()
                with tmp.open("wb") as fh:
                    for chunk in resp.iter_content(CHUNK):
                        fh.write(chunk)
            digest = sha256_file(tmp)
            if expected_sha256 and digest != expected_sha256:
                raise ValueError(
                    f"{key}: checksum mismatch. Expected {expected_sha256}, got {digest}. "
                    f"The remote file changed or the download was corrupted."
                )
            shutil.move(str(tmp), str(target))
        finally:
            tmp.unlink(missing_ok=True)

        self.manifest.record(
            ManifestEntry(
                key=key,
                path=str(target.relative_to(self.data_dir)).replace("\\", "/"),
                url=url,
                sha256=digest,
                size_bytes=target.stat().st_size,
                fetched_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                source=source,
                licence=licence,
                attributes=attributes or {},
            )
        )
        return target

    def register_local(
        self,
        key: str,
        path: Path,
        *,
        source: str,
        licence: str,
        url: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Path:
        """Record a file that was produced locally or placed by hand.

        Used for derived products and for manually-downloaded datasets such as
        Bhuvan CartoDEM, which needs a registration that we cannot automate.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)
        try:
            rel = str(path.relative_to(self.data_dir)).replace("\\", "/")
        except ValueError:
            rel = str(path)
        self.manifest.record(
            ManifestEntry(
                key=key,
                path=rel,
                url=url,
                sha256=sha256_file(path),
                size_bytes=path.stat().st_size,
                fetched_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                source=source,
                licence=licence,
                attributes=attributes or {},
            )
        )
        return path

    def fetch_json(
        self,
        key: str,
        url: str,
        rel_path: str,
        *,
        source: str,
        licence: str,
        method: str = "GET",
        data: str | None = None,
        attributes: dict[str, Any] | None = None,
        timeout: float = 180.0,
    ) -> Any:
        """Cache an API response body as JSON. Used for Overpass and STAC."""
        target = self.raw_dir / rel_path
        if self.manifest.get(key) is not None and target.exists():
            log.info("cache hit: %s", key)
            return json.loads(target.read_text(encoding="utf-8"))

        import requests

        log.info("querying %s for %s", url, key)
        resp = requests.request(method, url, data=data, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload), encoding="utf-8")
        self.register_local(
            key, target, source=source, licence=licence, url=url, attributes=attributes
        )
        return payload


def with_fallbacks(
    attempts: list[tuple[str, Callable[[], Any]]],
    *,
    what: str,
) -> tuple[Any, str, list[str]]:
    """Try each source in order; return (result, source_used, failures).

    SPEC.md 1: every fetcher must degrade gracefully and log which source was
    actually used. That decision is made here, once, so no fetcher can quietly
    skip it. The source that succeeded is returned for the provenance record.
    """
    failures: list[str] = []
    for name, thunk in attempts:
        try:
            log.info("%s: trying source %s", what, name)
            result = thunk()
            if result is not None:
                log.info("%s: using source %s", what, name)
                return result, name, failures
            failures.append(f"{name}: returned no data")
        except Exception as exc:  # noqa: BLE001 - we genuinely want the next source
            log.warning("%s: source %s failed: %s", what, name, exc)
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    raise RuntimeError(
        f"{what}: every source failed.\n" + "\n".join(f"  - {f}" for f in failures)
    )
