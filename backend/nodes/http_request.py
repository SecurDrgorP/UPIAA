from typing import Any
import httpx
import time
import socket
import ipaddress
import urllib.parse
import json
from core.node_registry import register_action
from core.expressions import resolve_template, apply_output_mapping

def check_ssrf_safe(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Invalid URL format")
        
    try:
        ip = socket.gethostbyname(hostname)
    except Exception as e:
        raise ValueError(f"DNS resolution failed for {hostname}: {e}")
        
    ip_obj = ipaddress.ip_address(ip)
    if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast or ip_obj.is_reserved:
        raise ValueError(f"SSRF Protection: Access to internal IP {ip} is blocked")

@register_action(
    type="http_request",
    label="HTTP Request",
    icon="🌐",
    description="Make an HTTP request to an external API",
    category="Actions",
    config_schema={
        "url": {
            "type": "string",
            "label": "URL",
            "required": True,
            "template": True,
            "placeholder": "https://api.example.com/data",
            "default": "https://api.example.com/data",
            "description": "The endpoint URL. Supports {{node_id.field}} interpolation.",
        },
        "method": {
            "type": "select",
            "label": "HTTP Method",
            "required": True,
            "default": "GET",
            "options": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
            "description": "The HTTP method to use.",
        },
        "headers": {
            "type": "object",
            "label": "Headers",
            "default": {},
            "description": "JSON object of request headers.",
        },
        "body": {
            "type": "object",
            "label": "Request Body",
            "default": {},
            "description": "JSON body payload (used with POST/PUT/PATCH).",
        },
        "output_mapping": {
            "type": "code",
            "label": "Output mapping",
            "required": False,
            "description": 'Rename or extract output keys before passing downstream. Example: {"summary": "response"}',
        },
    },
)
async def handle_http_request(config: dict[str, Any], input_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    raw_url = config.get("url", "")
    if not raw_url:
        return {"error": "missing field: url", "__input_error__": True}
        
    method = config.get("method", "GET").upper()
    headers_conf = config.get("headers", {})
    body_conf = config.get("body", {})

    # resolve templates
    url = resolve_template(raw_url, input_data)
    
    headers = {}
    if isinstance(headers_conf, dict):
        for k, v in headers_conf.items():
            if isinstance(v, str):
                headers[k] = resolve_template(v, input_data)
            else:
                headers[k] = str(v)
                
    body = {}
    if isinstance(body_conf, dict):
        for k, v in body_conf.items():
            if isinstance(v, str):
                body[k] = resolve_template(v, input_data)
            else:
                body[k] = v

    # SSRF protection
    try:
        check_ssrf_safe(url)
    except ValueError as e:
        return {"error": str(e), "__input_error__": True}

    start_time = time.time()
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            if method == "GET":
                resp = await client.get(url, headers=headers)
            elif method == "POST":
                resp = await client.post(url, headers=headers, json=body)
            elif method == "PUT":
                resp = await client.put(url, headers=headers, json=body)
            elif method == "PATCH":
                resp = await client.patch(url, headers=headers, json=body)
            elif method == "DELETE":
                resp = await client.delete(url, headers=headers)
            else:
                return {"error": f"Unsupported HTTP method: {method}", "__input_error__": True}
    except Exception as e:
        return {"error": f"HTTP Request failed: {e}", "__input_error__": True}

    elapsed_ms = int((time.time() - start_time) * 1000)

    # non-retryable ValueError on 4xx
    if 400 <= resp.status_code < 500:
        raise ValueError(f"Client error received: {resp.status_code} {resp.text}")

    try:
        response_body = resp.json()
    except Exception:
        response_body = resp.text

    result = {
        "status_code": resp.status_code,
        "response_body": response_body,
        "response_headers": dict(resp.headers),
        "elapsed_ms": elapsed_ms,
    }

    return apply_output_mapping(result, config.get("output_mapping", ""))
