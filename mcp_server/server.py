from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional

import httpx
from mcp.server.fastmcp import FastMCP, Context
from mcp.server.fastmcp.exceptions import ToolError

BASE_URL = os.getenv("LORE_INGEST_URL", "http://127.0.0.1:8099").rstrip("/")
mcp = FastMCP("lore-ingestor-mcp")


# ------------- tiny HTTP helpers (client per call) -------------
async def _get(path, params=None):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{BASE_URL}{path}", params=params)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        # raise ToolError so MCP shows a tool failure but doesn't spin out
        raise ToolError(f"HTTP GET {path} failed: {e}")


async def _post(path, json):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{BASE_URL}{path}", json=json)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        raise ToolError(f"HTTP POST {path} failed: {e}")


# ------------- tools -------------
@mcp.tool(description="Liveness check")
async def healthz(ctx: Context) -> Dict[str, Any]:
    return await _get("/v1/healthz")


@mcp.tool(description="Readiness check (writes to DB)")
async def readyz(ctx: Context) -> Dict[str, Any]:
    return await _get("/v1/readyz")


@mcp.tool(description="Supported parsers/extensions")
async def parsers(ctx: Context) -> Dict[str, Any]:
    return await _get("/v1/parsers")


@mcp.tool(description="Segmentation profiles")
async def profiles(ctx: Context) -> Dict[str, Any]:
    return await _get("/v1/profiles")


@mcp.tool(description="List works (searchable)")
async def works_list(
        ctx: Context,
        q: Optional[str] = None,
        author: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"limit": limit, "offset": offset}
    if q: params["q"] = q
    if author: params["author"] = author
    return await _get("/v1/works", params)


@mcp.tool(description="Get work summary")
async def work_get(ctx: Context, work_id: str) -> Dict[str, Any]:
    return await _get(f"/v1/works/{work_id}")


@mcp.tool(description="Scenes for a work")
async def scenes(ctx: Context, work_id: str) -> Dict[str, Any]:
    return await _get(f"/v1/works/{work_id}/scenes")


@mcp.tool(description="Chunks for a work")
async def chunks(ctx: Context, work_id: str) -> Dict[str, Any]:
    return await _get(f"/v1/works/{work_id}/chunks")


@mcp.tool(description="Exact substring [start,end)")
async def slice(ctx: Context, work_id: str, start: int, end: int) -> Dict[str, Any]:
    return await _get(f"/v1/works/{work_id}/slice", {"start": start, "end": end})


@mcp.tool(description="FTS search over chunk text")
async def search(
        ctx: Context,
        q: str,
        work_id: Optional[str] = None,
        limit: int = 25,
        offset: int = 0,
        rebuild: bool = False,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"q": q, "limit": limit, "offset": offset, "rebuild": "true" if rebuild else "false"}
    if work_id: params["work_id"] = work_id
    return await _get("/v1/search", params)


@mcp.tool(description="Ingest a file by absolute path (server reads the path)")
async def ingest(
        ctx: Context,
        path: str,
        title: Optional[str] = None,
        author: Optional[str] = None,
        profile: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"path": path}
    if title: payload["title"] = title
    if author: payload["author"] = author
    if profile: payload["profile"] = profile
    return await _post("/v1/ingest", payload)


@mcp.tool(description="Force re-segmentation for an existing work")
async def resegment(
        ctx: Context,
        work_id: str,
        profile: Optional[str] = None,
        window_chars: int = 512,
        stride_chars: int = 384,
) -> Dict[str, Any]:
    payload = {"profile": profile, "window_chars": window_chars, "stride_chars": stride_chars}
    return await _post(f"/v1/works/{work_id}/resegment", payload)


# ------------- compatibility runner -------------
def main() -> None:
    """
    Try the modern SDK entrypoint first; fall back to older APIs; finally suggest upgrade.
    """
    # Newer SDKs
    if hasattr(mcp, "run_stdio"):
        print("[lore-ingestor-mcp] waiting on stdio", file=sys.stderr, flush=True)
        mcp.run_stdio()
        return

    # Some older SDKs expose a generic async runner
    run_fn = getattr(mcp, "run", None) or getattr(mcp, "serve", None)
    if run_fn is not None:
        try:
            # try sync call with transport selector
            run_fn("stdio")
            return
        except TypeError:
            # likely async; run it
            import anyio
            anyio.run(run_fn)
            return

    raise RuntimeError(
        "Your installed 'mcp' SDK does not expose a stdio runner on FastMCP.\n"
        "Upgrade with: pip install -U 'mcp>=0.2.0' httpx"
    )


if __name__ == "__main__":
    main()
