#!/usr/bin/env python3
"""dbchub MCP server: share the signal databases with MCP clients over HTTP.

A read-only Model Context Protocol server on top of the *same* static read API the
website serves (index.json, <name>.structure.json, search-index.json, the raw files
and their exports). Writes still go through Pull Requests, as everywhere else.

The data source is either
  * a local build output directory (default: site/, produced by `dbchub-build`), or
  * the published site's base URL, e.g. https://dbchub.com/
so the server can run anywhere, with no checkout and no DBC parsers.

    uv run dbchub-build                                   # once, for a local source
    uv run dbchub-mcp                                     # http://127.0.0.1:8765/mcp
    uv run dbchub-mcp --source https://dbchub.com/ --host 0.0.0.0 \\
        --allowed-host mcp.example.com --token "$DBCHUB_MCP_TOKEN"

Clients connect with the Streamable HTTP transport at `/mcp`. `--transport stdio`
runs it as a local subprocess server instead. `GET /healthz` is a liveness probe.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin

import httpx2
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO / "site"
DEFAULT_PORT = 8765
MAX_CHARS_CAP = 1_000_000  # hard ceiling for one get_database_file chunk


# --------------------------------------------------------------------------- data


class Store:
    """Reads the generated read-API files from a local directory or a base URL,
    with a small TTL cache (the catalog only changes when the site is rebuilt)."""

    def __init__(self, source: str, ttl: float = 300.0, cache_size: int = 64):
        self.remote = source.startswith(("http://", "https://"))
        if self.remote:
            self.base = source if source.endswith("/") else source + "/"
            self.http = httpx2.AsyncClient(timeout=30.0, follow_redirects=True,
                                          headers={"User-Agent": "dbchub-mcp"})
        else:
            self.root = Path(source).resolve()
            if not (self.root / "index.json").is_file():
                raise SystemExit(f"error: {self.root}/index.json not found; run `uv run dbchub-build` "
                                 "first, or pass --source <published site URL>")
        self.ttl = ttl
        self.cache_size = cache_size
        self._cache: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def public_url(self, rel: str) -> str | None:
        return urljoin(self.base, rel) if self.remote else None

    async def _read(self, rel: str) -> bytes:
        if self.remote:
            r = await self.http.get(urljoin(self.base, rel))
            if r.status_code == 404:
                raise ToolError(f"not found: {rel}")
            r.raise_for_status()
            return r.content
        p = (self.root / rel).resolve()
        if not p.is_relative_to(self.root):  # defence in depth; paths come from the catalog
            raise ToolError("invalid path")
        try:
            return await asyncio.to_thread(p.read_bytes)
        except FileNotFoundError:
            raise ToolError(f"not found: {rel}") from None

    async def get(self, rel: str, *, as_json: bool = True) -> Any:
        hit = self._cache.get(rel)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        async with self._locks.setdefault(rel, asyncio.Lock()):
            hit = self._cache.get(rel)
            if hit and time.monotonic() - hit[0] < self.ttl:
                return hit[1]
            raw = await self._read(rel)
            value = json.loads(raw) if as_json else raw.decode("utf-8", errors="replace")
            if len(self._cache) >= self.cache_size:  # evict the oldest entry
                self._cache.pop(min(self._cache, key=lambda k: self._cache[k][0]))
            self._cache[rel] = (time.monotonic(), value)
            return value

    # ---- catalog helpers

    async def catalog(self) -> list[dict]:
        return (await self.get("index.json"))["databases"]

    async def record(self, path: str) -> dict[str, Any]:
        """Resolve a database by catalog path (e.g. `acura/ilx-2016/can/x.dbc`) or,
        if unambiguous, by bare name (`x`)."""
        path = path.strip().removeprefix("/").removeprefix("databases/")
        dbs = await self.catalog()
        for r in dbs:
            if r["path"] == path:
                return r
        named = [r for r in dbs if r["name"] == path]
        if len(named) == 1:
            return named[0]
        if named:
            raise ToolError(f"'{path}' is ambiguous; use one of: " + ", ".join(r["path"] for r in named))
        raise ToolError(f"no database '{path}'; use list_databases to find its path")

    async def structure(self, rec: dict) -> dict[str, Any]:
        if not rec.get("structure_url"):
            raise ToolError(f"{rec['path']} could not be parsed, so it has no structure")
        rel = PurePosixPath("databases", rec["path"]).with_suffix(".structure.json")
        return await self.get(rel.as_posix())


def _hex_id(msg_id: int, extended: bool) -> str:
    return f"0x{msg_id:08X}" if extended else f"0x{msg_id:03X}"


def _parse_id(text: str) -> int | None:
    t = text.strip().lower()
    try:
        return int(t, 16) if t.startswith("0x") else int(t)
    except ValueError:
        return None


def _summary(r: dict) -> dict[str, Any]:
    return {
        "path": r["path"], "name": r["name"], "format": r["format"],
        "manufacturer": r["manufacturer"], "manufacturer_name": r.get("manufacturer_name"),
        "model": r["model"], "bus": r["bus"], "license": r.get("license"),
        "counts": r.get("counts"), "valid": r.get("valid"), "updated": r.get("updated"),
    }


def _message_row(m: dict) -> dict[str, Any]:
    return {
        "id": m["id"], "id_hex": _hex_id(m["id"], m["extended"]), "extended": m["extended"],
        "name": m["name"], "length": m["length"], "fd": m.get("fd", False),
        "senders": m.get("senders") or [], "cycle_ms": m.get("cycle_ms"),
        "signals": len(m.get("signals") or []),
    }


# ------------------------------------------------------------------------- server

INSTRUCTIONS = """\
dbchub is a catalog of automotive signal databases (DBC, ARXML, LDF, DBF, KCD, SYM),
organised as <manufacturer>/<model>/<bus>/<file>. Everything here is read-only.

Typical flow: list_databases (or list_manufacturers) to find a database's `path`,
then list_messages / get_message for its frames and signals (bit layout, scaling,
units, value tables). search_signals finds a signal or message across every database.
get_database_file returns the raw file or a converted export. Signal data is
community-contributed and NOT suitable for safety-critical use; respect each file's
license (see get_database)."""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                            idempotent_hint=True, open_world_hint=False)


def build_server(store: Store) -> MCPServer:
    mcp = MCPServer(name="dbchub", title="dbchub signal databases", instructions=INSTRUCTIONS,
                    version="0.1.0")

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    async def list_databases(query: str = "", manufacturer: str = "", model: str = "",
                             bus: str = "", format: str = "", valid_only: bool = False,
                             limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """List signal databases, optionally filtered. `query` matches (case-insensitively)
        the file name, path, manufacturer or model; the other filters are exact
        (e.g. manufacturer="toyota", bus="can", format="dbc")."""
        q = query.strip().lower()
        out = []
        for r in await store.catalog():
            if manufacturer and r["manufacturer"] != manufacturer.lower():
                continue
            if model and r["model"] != model.lower():
                continue
            if bus and r["bus"] != bus.lower():
                continue
            if format and r["format"] != format.lower().lstrip("."):
                continue
            if valid_only and not r.get("valid"):
                continue
            hay = " ".join(str(r.get(k) or "") for k in
                           ("path", "name", "manufacturer_name", "model_name")).lower()
            if q and q not in hay:
                continue
            out.append(_summary(r))
        limit = max(1, min(limit, 500))
        return {"total": len(out), "offset": offset, "results": out[offset:offset + limit]}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    async def list_manufacturers() -> dict[str, Any]:
        """Every manufacturer with its models, buses and database counts."""
        mf: dict[str, dict] = {}
        for r in await store.catalog():
            m = mf.setdefault(r["manufacturer"], {"name": r.get("manufacturer_name"),
                                                  "databases": 0, "models": {}})
            m["databases"] += 1
            buses = m["models"].setdefault(r["model"], set())
            buses.add(r["bus"])
        return {k: {**v, "models": {mk: sorted(b) for mk, b in sorted(v["models"].items())}}
                for k, v in sorted(mf.items())}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    async def get_database(path: str) -> dict[str, Any]:
        """Full metadata for one database: format, counts, license and attribution,
        source, validation errors/warnings, lint summary, available export formats
        and versions. `path` is the catalog path from list_databases."""
        r = await store.record(path)
        rel = f"databases/{r['path']}"
        return {
            **{k: v for k, v in r.items() if not k.endswith("_url") and k not in ("download", "exports")},
            "exports": [e["format"] for e in r.get("exports") or []],
            "download_url": store.public_url(rel),
        }

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    async def list_messages(path: str, query: str = "", limit: int = 500) -> dict[str, Any]:
        """The frames of a database: ID (decimal and hex), name, DLC, senders, cycle
        time and signal count. `query` filters by name substring or exact ID
        (decimal or 0x-hex)."""
        r = await store.record(path)
        s = await store.structure(r)
        q, qid = query.strip().lower(), _parse_id(query) if query.strip() else None
        msgs = [m for m in s["messages"]
                if not q or q in m["name"].lower() or (qid is not None and m["id"] == qid)]
        msgs.sort(key=lambda m: m["id"])
        return {"path": r["path"], "nodes": [n["name"] for n in s.get("nodes") or []],
                "total": len(msgs), "messages": [_message_row(m) for m in msgs[:max(1, limit)]]}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    async def get_message(path: str, message: str) -> dict[str, Any]:
        """One frame with all its signals: start bit, length, byte order, signedness,
        factor/offset, min/max, unit, receivers, multiplexing, comments and value
        tables. `message` is the frame name or its ID (decimal or 0x-hex)."""
        r = await store.record(path)
        s = await store.structure(r)
        mid = _parse_id(message)
        hits = [m for m in s["messages"]
                if m["name"] == message or (mid is not None and m["id"] == mid)]
        if not hits:
            hits = [m for m in s["messages"] if m["name"].lower() == message.strip().lower()]
        if not hits:
            raise ToolError(f"no message '{message}' in {r['path']}; see list_messages")
        return {"path": r["path"], "messages": [{**m, "id_hex": _hex_id(m["id"], m["extended"])}
                                                for m in hits]}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    async def search_signals(query: str, kind: str = "any", manufacturer: str = "",
                             limit: int = 50) -> dict[str, Any]:
        """Search every database at once. Matches signal names and/or message names by
        case-insensitive substring, or messages by exact ID (decimal or 0x-hex).
        `kind` is "signal", "message" or "any"."""
        if not query.strip():
            raise ToolError("query is required")
        if kind not in ("signal", "message", "any"):
            raise ToolError('kind must be "signal", "message" or "any"')
        idx = await store.get("search-index.json")
        dbs, msgs, sigs = idx["databases"], idx["messages"], idx["signals"]
        q, qid = query.strip().lower(), _parse_id(query)
        mf = manufacturer.lower()
        limit = max(1, min(limit, 500))

        def db_ok(d: list) -> bool:  # [path, name, manufacturer_name, model_name, bus]
            return not mf or d[0].split("/", 1)[0] == mf

        def msg_ref(mi: int) -> dict[str, Any]:
            db, mid, ext, name = msgs[mi]
            d = dbs[db]
            return {"path": d[0], "manufacturer": d[2], "model": d[3], "bus": d[4],
                    "message": name, "id": mid, "id_hex": _hex_id(mid, bool(ext))}

        results, total = [], 0
        if kind in ("message", "any"):
            for i, (db, mid, _ext, name) in enumerate(msgs):
                if db_ok(dbs[db]) and (q in name.lower() or (qid is not None and mid == qid)):
                    total += 1
                    if len(results) < limit:
                        results.append({"type": "message", **msg_ref(i)})
        if kind in ("signal", "any"):
            for mi, name, unit in sigs:
                if q in name.lower() and db_ok(dbs[msgs[mi][0]]):
                    total += 1
                    if len(results) < limit:
                        results.append({"type": "signal", "signal": name, "unit": unit or None,
                                        **msg_ref(mi)})
        return {"total": total, "results": results}

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    async def get_database_file(path: str, format: str = "", offset: int = 0,
                                max_chars: int = 100_000) -> dict[str, Any]:
        """The database file as text: the original upload, or (with `format`, e.g.
        "dbc", "kcd", "sym") one of the converted exports listed by get_database.
        Large files are returned in chunks: pass the returned `next_offset` back
        as `offset` to continue."""
        r = await store.record(path)
        if format and format.lower() != r["format"]:
            exp = next((e for e in r.get("exports") or [] if e["format"] == format.lower()), None)
            if not exp:
                avail = ", ".join([r["format"]] + [e["format"] for e in r.get("exports") or []])
                raise ToolError(f"no '{format}' version of {r['path']}; available: {avail}")
            rel = f"databases/{exp['path']}"
        else:
            rel = f"databases/{r['path']}"
        text = await store.get(rel, as_json=False)
        offset = max(0, offset)
        end = offset + max(1, min(max_chars, MAX_CHARS_CAP))
        return {
            "path": rel.removeprefix("databases/"), "license": r.get("license"),
            "attribution": r.get("attribution"), "total_chars": len(text),
            "offset": offset, "next_offset": end if end < len(text) else None,
            "url": store.public_url(rel), "content": text[offset:end],
        }

    @mcp.tool(annotations=READ_ONLY, structured_output=True)
    async def get_history(path: str, limit: int = 10) -> dict[str, Any]:
        """Committed versions of a database (newest first) with what each changed:
        messages/signals added, removed or modified."""
        r = await store.record(path)
        rel = PurePosixPath("databases", r["path"]).with_suffix(".history.json").as_posix()
        h = await store.get(rel)
        return {"path": r["path"], "versions": h.get("versions", [])[:max(1, limit)]}

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_request):
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True})

    return mcp


# ---------------------------------------------------------------------- transport


class BearerAuth:
    """ASGI middleware: require `Authorization: Bearer <token>` (except /healthz)."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/healthz":
            auth = dict(scope["headers"]).get(b"authorization", b"")
            scheme, _, given = auth.partition(b" ")
            if scheme.lower() != b"bearer" or not hmac.compare_digest(given.strip(), self.token):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
        await self.app(scope, receive, send)


def main() -> int:
    ap = argparse.ArgumentParser(description="Serve the dbchub databases over MCP (read-only).")
    ap.add_argument("--source", default=os.environ.get("DBCHUB_SOURCE", str(DEFAULT_SOURCE)),
                    help="built site directory or published base URL (default: site/; env DBCHUB_SOURCE)")
    ap.add_argument("--transport", choices=("http", "stdio"), default="http")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (0.0.0.0 for remote access)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    ap.add_argument("--path", default="/mcp", help="MCP endpoint path (default: /mcp)")
    ap.add_argument("--token", default=os.environ.get("DBCHUB_MCP_TOKEN"),
                    help="require this bearer token (env DBCHUB_MCP_TOKEN)")
    ap.add_argument("--allowed-host", action="append", default=[], metavar="HOST",
                    help="public Host header(s) to accept, e.g. mcp.example.com (DNS-rebinding "
                         "protection); repeatable")
    ap.add_argument("--allowed-origin", action="append", default=[], metavar="ORIGIN",
                    help="browser Origin(s) to accept, e.g. https://app.example.com; repeatable")
    ap.add_argument("--cache-ttl", type=float, default=300.0, help="seconds to cache catalog files")
    args = ap.parse_args()

    store = Store(args.source, ttl=args.cache_ttl)
    server = build_server(store)

    if args.transport == "stdio":
        server.run("stdio")
        return 0

    import uvicorn

    security = None
    if args.allowed_host or args.allowed_origin:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=args.allowed_host + ["127.0.0.1:*", "localhost:*"],
            allowed_origins=args.allowed_origin,
        )
    app = server.streamable_http_app(streamable_http_path=args.path, stateless_http=True,
                                     json_response=True, transport_security=security,
                                     host=args.host)
    if args.token:
        app = BearerAuth(app, args.token)
    elif args.host not in ("127.0.0.1", "localhost", "::1"):
        print("warning: listening on a public interface without --token; anyone who can reach "
              "it can read the catalog (it is public data, but consider a token or a proxy)",
              file=sys.stderr)
    where = args.source if store.remote else store.root
    print(f"dbchub MCP server: http://{args.host}:{args.port}{args.path}  (source: {where})",
          file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
