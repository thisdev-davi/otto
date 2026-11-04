"""Serve authorized local/object media with identical safety and range behavior."""

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response

from app.services import storage


async def serve(
    locator: str, media_type: str, headers: dict[str, str], request: Request
) -> Response:
    if not storage.is_remote(locator):
        if not await storage.exists(locator):
            raise HTTPException(status_code=404)
        return FileResponse(locator, media_type=media_type, headers=headers)
    try:
        data = await storage.read(locator)
    except FileNotFoundError:
        raise HTTPException(status_code=404) from None
    headers = {**headers, "Accept-Ranges": "bytes"}
    requested = request.headers.get("range")
    if requested:
        # Single byte ranges suffice for browser audio/video seeking. Reject multiple
        # ranges rather than accidentally returning a misleading partial response.
        try:
            unit, interval = requested.split("=", 1)
            first, last = interval.split("-", 1)
            if unit != "bytes" or "," in interval:
                raise ValueError
            start = int(first) if first else max(0, len(data) - int(last))
            end = min(int(last), len(data) - 1) if first and last else len(data) - 1
            if start < 0 or start > end or start >= len(data):
                raise ValueError
        except ValueError:
            return Response(
                status_code=416,
                headers={**headers, "Content-Range": f"bytes */{len(data)}"},
            )
        headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
        return Response(
            data[start : end + 1],
            status_code=206,
            media_type=media_type,
            headers=headers,
        )
    return Response(data, media_type=media_type, headers=headers)
