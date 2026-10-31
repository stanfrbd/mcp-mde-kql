"""Paced, resumable table-existence checks; event content never enters this cache."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict

from mcp_mde_kql.utils.config import MdeConfig

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_MAX_TABLES = 4096
_MAX_CACHE_BYTES = 2 * 1024 * 1024


class PresenceClient(Protocol):
    async def table_names(self) -> list[str]: ...
    async def query(self, query: str, timespan: str | None = None) -> dict[str, Any]: ...


class _Entry(TypedDict):
    state: Literal["with_data", "empty", "unknown"]
    checked_at: float


class TablePresenceCache:
    def __init__(
        self, client: PresenceClient, config: MdeConfig, *, _probe_interval: float | None = None
    ) -> None:
        self.client = client
        self.config = config
        self._interval = (
            config.presence_request_interval if _probe_interval is None else _probe_interval
        )
        self._identity = hashlib.sha256(
            f"{config.tenant_id}:{config.client_id}".encode()
        ).hexdigest()
        self._tables: list[str] = []
        self._entries: dict[str, _Entry] = {}
        self._status = "idle"
        self._warnings: list[str] = []
        self._task: asyncio.Task[None] | None = None
        self._last_probe: float | None = None
        self._pass_finished = asyncio.Event()

    async def start(self) -> None:
        """Load recent progress and launch a background worker without waiting for probes."""
        if self._task is not None:
            return
        self._load()
        self._status = "warming"
        self._task = asyncio.create_task(self._run(), name="table-presence-warmup")

    async def close(self) -> None:
        """Stop outstanding requests and pacing waits promptly; completed probes are persisted."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def snapshot(self) -> dict[str, Any]:
        """Return bounded names/counts, including pending and expired entries as unknown."""
        now = time.time()
        groups: dict[str, list[str]] = {"with_data": [], "empty": [], "unknown": []}
        checked = 0
        errors = 0
        for table in self._tables:
            entry = self._entries.get(table)
            fresh = entry is not None and self._fresh(entry, now)
            state = entry["state"] if fresh and entry is not None else "unknown"
            groups[state].append(table)
            checked += bool(fresh)
            errors += bool(fresh and state == "unknown")
        warnings = list(self._warnings)
        if self._status == "warming":
            warnings.append("Presence warmup in progress; the list contains verified tables only.")
        return {
            "status": self._status,
            "window": "P7D",
            "total_tables": len(self._tables),
            "checked_tables": checked,
            "with_data": groups["with_data"],
            "empty": groups["empty"],
            "unknown": groups["unknown"],
            "with_data_count": len(groups["with_data"]),
            "empty_count": len(groups["empty"]),
            "unknown_count": len(groups["unknown"]),
            "error_count": errors,
            "complete": self._status == "ready" and not groups["unknown"],
            "warnings": warnings,
        }

    def _fresh(self, entry: _Entry, now: float) -> bool:
        return 0 <= now - entry["checked_at"] < self.config.presence_cache_ttl

    async def _run(self) -> None:
        while True:
            self._pass_finished.clear()
            try:
                await self._refresh()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Background failures must never expose event values, credentials or API bodies.
                self._status = "error"
                self._warn(
                    "Table presence discovery failed; unknown tables must not be treated as empty."
                )
            finally:
                self._pass_finished.set()
            delay = (
                min(self.config.presence_cache_ttl, 60)
                if self._status == "error"
                else self.config.presence_cache_ttl
            )
            known_times = [
                e["checked_at"] for e in self._entries.values() if e["state"] != "unknown"
            ]
            if known_times and self._status != "error":
                delay = max(1, min(known_times) + self.config.presence_cache_ttl - time.time())
            await asyncio.sleep(delay)

    async def _refresh(self) -> None:
        self._status = "warming"
        async with asyncio.timeout(30):
            tables = await self.client.table_names()
        if (
            not isinstance(tables, list)
            or len(tables) > _MAX_TABLES
            or any(not isinstance(name, str) or not _IDENTIFIER.fullmatch(name) for name in tables)
        ):
            raise ValueError("Invalid table catalog")
        self._tables = sorted(set(tables))
        self._entries = {
            name: entry for name, entry in self._entries.items() if name in self._tables
        }
        self._save()
        consecutive_errors = 0
        for table in self._tables:
            entry = self._entries.get(table)
            if (
                entry is not None
                and entry["state"] != "unknown"
                and self._fresh(entry, time.time())
            ):
                continue
            if self._last_probe is not None:
                remaining = self._interval - (time.monotonic() - self._last_probe)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._last_probe = time.monotonic()
            state: Literal["with_data", "empty", "unknown"] = "unknown"
            try:
                async with asyncio.timeout(15):
                    result = await self.client.query(
                        f"{table} | take 1 | project HasData=true", timespan="P7D"
                    )
                rows = result["results"]
                if not isinstance(rows, list) or len(rows) > 1:
                    raise ValueError("Invalid presence response")
                if rows:
                    # Only the projected constant is accepted; no original event fields are saved.
                    if not isinstance(rows[0], dict):
                        raise ValueError("Invalid presence response")
                    value = rows[0].get("HasData")
                    # Graph encodes KQL booleans as SByte (1) in some hunting responses.
                    if value is not True and not (type(value) is int and value == 1):
                        raise ValueError("Invalid presence response")
                    state = "with_data"
                else:
                    state = "empty"
            except asyncio.CancelledError:
                raise
            except Exception:
                self._warn("Some table checks failed; unknown tables must not be treated as empty.")
            self._entries[table] = {"state": state, "checked_at": time.time()}
            self._save()
            consecutive_errors = consecutive_errors + 1 if state == "unknown" else 0
            if consecutive_errors >= 3:
                self._status = "error"
                self._warn(
                    "Presence warmup paused after repeated failures; remaining tables are still pending."
                )
                return
        self._status = "ready"

    def _warn(self, message: str) -> None:
        if message not in self._warnings:
            self._warnings.append(message)

    def _load(self) -> None:
        path = self.config.presence_cache_path
        if path is None:
            return
        try:
            if path.stat().st_size > _MAX_CACHE_BYTES:
                return
            saved = json.loads(path.read_text(encoding="utf-8"))
            if (
                saved.get("identity") != self._identity
                or saved.get("window") != "P7D"
                or saved.get("version") != 1
            ):
                return
            entries = saved["entries"]
            if not isinstance(entries, dict) or len(entries) > _MAX_TABLES:
                return
            loaded: dict[str, _Entry] = {}
            for name, entry in entries.items():
                if not _IDENTIFIER.fullmatch(name) or not isinstance(entry, dict):
                    return
                state = entry.get("state")
                stamp = entry.get("checked_at")
                if (
                    state not in ("with_data", "empty", "unknown")
                    or not isinstance(stamp, (int, float))
                    or not math.isfinite(stamp)
                ):
                    return
                valid: _Entry = {"state": state, "checked_at": float(stamp)}
                if self._fresh(valid, time.time()):
                    loaded[name] = valid
            self._entries = loaded
            self._tables = sorted(loaded)
            if loaded:
                # Preserve pacing across short process restarts as well as within one worker.
                elapsed = time.time() - max(e["checked_at"] for e in loaded.values())
                if elapsed < self._interval:
                    self._last_probe = time.monotonic() - elapsed
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            # Stale, partial, corrupt or inaccessible caches never become negative evidence.
            return

    def _save(self) -> None:
        path = self.config.presence_cache_path
        if path is None:
            return
        temp_path: Path | None = None
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor, filename = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            temp_path = Path(filename)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(
                    {
                        "version": 1,
                        "identity": self._identity,
                        "window": "P7D",
                        "entries": self._entries,
                    },
                    stream,
                    separators=(",", ":"),
                )
            temp_path.replace(path)
        except OSError:
            self._warn(
                "Table presence persistence unavailable; progress will remain in memory for this session."
            )
        finally:
            if temp_path is not None:
                with contextlib.suppress(OSError):
                    temp_path.unlink(missing_ok=True)
