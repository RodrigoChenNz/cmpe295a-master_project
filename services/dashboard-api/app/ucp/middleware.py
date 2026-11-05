"""UCP errors, trace IDs and optional Ed25519 response signatures."""

import json
from fastapi import Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware
from ..config import settings
from .schemas import VERSION
from .signatures import sign


class UCPMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        public = (
            request.url.path.startswith("/ucp/v1/")
            and request.url.path != "/ucp/v1/webhooks/orders"
        )
        response = await call_next(request)
        if not public and not request.url.path.startswith("/.well-known/ucp"):
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        headers = dict(response.headers)
        if public:
            try:
                payload = json.loads(body)
            except ValueError:
                payload = {}
            if response.status_code >= 400 or "detail" in payload:
                detail = payload.get("detail", "Request failed.")
                if isinstance(detail, dict):
                    code = detail.get("code", "request_invalid")
                    content = detail.get("content", str(detail))
                else:
                    content = detail if isinstance(detail, str) else "Invalid request fields."
                    code = (
                        content.split(":", 1)[0]
                        if ":" in content
                        else {
                            401: "unauthorized",
                            403: "forbidden",
                            404: "not_found",
                            409: "conflict",
                            422: "request_invalid",
                            429: "rate_limited",
                            503: "unavailable",
                        }.get(response.status_code, "request_invalid")
                    )
                payload = {
                    "ucp": {"version": VERSION, "status": "error", "capabilities": {}},
                    "messages": [
                        {
                            "type": "error",
                            "code": code,
                            "content": content,
                            "severity": "recoverable",
                        }
                    ],
                    "continue_url": settings.ucp_base_url.rstrip("/") + "/shop",
                }
                body = json.dumps(payload, separators=(",", ":")).encode()
            headers["Request-Id"] = request.headers.get("request-id", "")
        headers.pop("content-length", None)
        if settings.ucp_signing_key_file:
            headers.update(
                sign(
                    body,
                    {
                        "@status": str(response.status_code),
                        **(
                            {"request-id": headers["Request-Id"]}
                            if headers.get("Request-Id")
                            else {}
                        ),
                    },
                    settings.ucp_signing_key_file,
                    kid="shelfiq-business",
                )
            )
        return Response(
            body,
            status_code=response.status_code,
            headers=headers,
            media_type="application/json",
        )
