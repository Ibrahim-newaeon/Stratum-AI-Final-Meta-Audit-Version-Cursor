# =============================================================================
# Stratum AI - Google Tag Manager Service (tag deployment only)
# =============================================================================
"""
Google Tag Manager helpers.

GTM is a *tag deployment* integration, not an ad channel: a web container
deploys the Meta Pixel and the Stratum tracking snippet, and a server-side
tagging endpoint (sGTM) forwards events to the Meta Conversions API and to
the Stratum CDP through the ``sgtm`` source (``POST /api/v1/cdp/ingest`` with
``X-Source-Key``).

Provides:
- container id / server URL validation
- head/body/Stratum snippet generation
- CDP ``sgtm`` source linkage
- lightweight reachability verification of both containers
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.cdp import CDPSource, SourceType

logger = get_logger(__name__)

GTM_CONTAINER_ID_RE = re.compile(r"^GTM-[A-Z0-9]{4,10}$")

GTM_PUBLIC_HOST = "https://www.googletagmanager.com"
SOURCE_HEADER = "X-Source-Key"
SGTM_SOURCE_NAME = "Server-side GTM"
SGTM_ROLE = "tag_deployment"
META_CAPI_CLIENT_LABEL = "Meta Conversions API tag (server container)"


# =============================================================================
# Validation
# =============================================================================


def validate_container_id(value: str) -> str:
    """
    Normalise and validate a GTM container id (``GTM-XXXXXXX``).

    Raises:
        ValueError: when the id does not match ``GTM-[A-Z0-9]{4,10}``.
    """
    normalised = (value or "").strip().upper()
    if not GTM_CONTAINER_ID_RE.match(normalised):
        raise ValueError("Invalid GTM container id, expected GTM-XXXXXXX")
    return normalised


def validate_server_container_url(url: str) -> str:
    """
    Validate a server-side tagging endpoint.

    Must be ``https://`` with a hostname, no query string or fragment; the
    trailing slash is stripped.

    Raises:
        ValueError: on any violation.
    """
    candidate = (url or "").strip()
    if not candidate:
        raise ValueError("Server container URL is required")
    parts = urlsplit(candidate)
    if parts.scheme != "https":
        raise ValueError("Server container URL must use https://")
    if not parts.hostname:
        raise ValueError("Server container URL must include a hostname")
    if parts.query or parts.fragment:
        raise ValueError("Server container URL must not contain a query string or fragment")
    if parts.username or parts.password:
        raise ValueError("Server container URL must not contain credentials")
    if any(ch.isspace() for ch in candidate):
        raise ValueError("Server container URL must not contain whitespace")
    return candidate.rstrip("/")


# =============================================================================
# Snippets
# =============================================================================


@dataclass
class GTMSnippets:
    """Generated GTM snippets and server-side tagging configuration."""

    head_snippet: str
    body_snippet: str
    stratum_snippet: str
    sgtm_config: dict[str, Any]


@dataclass
class GTMVerifyResult:
    """Result of verifying GTM containers are reachable."""

    success: bool
    message: str
    web_container_ok: bool | None
    server_container_ok: bool | None
    checked_at: datetime


def _js_string(value: str) -> str:
    """Safely embed a Python string into JavaScript source (JSON-escaped, quoted)."""
    return json.dumps(value or "")


def _head_snippet(web_container_id: str, loader_base: str) -> str:
    """Standard GTM head snippet, optionally loading gtm.js from the server container."""
    return (
        "<!-- Google Tag Manager -->\n"
        "<script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':\n"
        "new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],\n"
        "j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=\n"
        f"'{loader_base}/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);\n"
        f"}})(window,document,'script','dataLayer','{web_container_id}');</script>\n"
        "<!-- End Google Tag Manager -->"
    )


def _body_snippet(web_container_id: str, loader_base: str) -> str:
    """Standard GTM noscript body snippet."""
    return (
        "<!-- Google Tag Manager (noscript) -->\n"
        f'<noscript><iframe src="{loader_base}/ns.html?id={web_container_id}"\n'
        'height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>\n'
        "<!-- End Google Tag Manager (noscript) -->"
    )


def _stratum_snippet(stratum_ingest_url: str, source_key: str | None) -> str:
    """
    Vanilla-JS dataLayer listener that forwards custom dataLayer events to
    the Stratum CDP ingest endpoint as ``{events: [...]}`` with ``X-Source-Key``.
    """
    ingest = _js_string(stratum_ingest_url)
    key = _js_string(source_key or "")
    header = _js_string(SOURCE_HEADER)
    return (
        "<!-- Stratum AI tracking snippet (dataLayer -> Stratum CDP) -->\n"
        "<script>\n"
        "(function(w,d){\n"
        f"  var INGEST_URL={ingest};\n"
        f"  var SOURCE_KEY={key};\n"
        f"  var SOURCE_HEADER={header};\n"
        "  if(!INGEST_URL||!SOURCE_KEY){return;}\n"
        "  var dl=w.dataLayer=w.dataLayer||[];\n"
        "  function anonId(){\n"
        "    try{var k='stratum_anon_id';var v=w.localStorage.getItem(k);\n"
        "      if(!v){v='anon_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,12);\n"
        "        w.localStorage.setItem(k,v);}\n"
        "      return v;}catch(e){return 'anon_'+Math.random().toString(36).slice(2,14);}\n"
        "  }\n"
        "  function toEvent(item){\n"
        "    if(!item||typeof item!=='object'||!item.event){return null;}\n"
        "    var name=String(item.event);\n"
        "    if(name.indexOf('gtm.')===0){return null;}\n"
        "    var props={};\n"
        "    for(var k in item){if(Object.prototype.hasOwnProperty.call(item,k)&&k!=='event'){props[k]=item[k];}}\n"
        "    var ids=[{type:'anonymous_id',value:anonId()}];\n"
        "    if(item.email){ids.push({type:'email',value:String(item.email)});}\n"
        "    if(item.phone){ids.push({type:'phone',value:String(item.phone)});}\n"
        "    if(item.user_id){ids.push({type:'external_id',value:String(item.user_id)});}\n"
        "    return {event_name:name,event_time:new Date().toISOString(),\n"
        "      idempotency_key:name+'_'+Date.now()+'_'+Math.random().toString(36).slice(2,8),\n"
        "      identifiers:ids,properties:props,\n"
        "      context:{page_url:w.location.href,referrer:d.referrer,user_agent:w.navigator.userAgent,\n"
        "        locale:w.navigator.language}};\n"
        "  }\n"
        "  function send(ev){\n"
        "    try{\n"
        "      var headers={'Content-Type':'application/json'};headers[SOURCE_HEADER]=SOURCE_KEY;\n"
        "      var body=JSON.stringify({events:[ev]});\n"
        "      if(w.fetch){w.fetch(INGEST_URL,{method:'POST',headers:headers,body:body,keepalive:true})\n"
        "        .catch(function(){});}\n"
        "      else{var x=new XMLHttpRequest();x.open('POST',INGEST_URL,true);\n"
        "        x.setRequestHeader('Content-Type','application/json');\n"
        "        x.setRequestHeader(SOURCE_HEADER,SOURCE_KEY);x.send(body);}\n"
        "    }catch(e){}\n"
        "  }\n"
        "  var originalPush=dl.push;\n"
        "  dl.push=function(){\n"
        "    var args=Array.prototype.slice.call(arguments);\n"
        "    var result=originalPush.apply(dl,args);\n"
        "    for(var i=0;i<args.length;i++){var ev=toEvent(args[i]);if(ev){send(ev);}}\n"
        "    return result;\n"
        "  };\n"
        "  for(var i=0;i<dl.length;i++){var existing=toEvent(dl[i]);if(existing){send(existing);}}\n"
        "})(window,document);\n"
        "</script>\n"
        "<!-- End Stratum AI tracking snippet -->"
    )


def build_snippets(
    web_container_id: str | None,
    server_container_url: str | None,
    stratum_ingest_url: str,
    source_key: str | None,
    meta_pixel_id: str | None,
) -> GTMSnippets:
    """
    Build the GTM head/body snippets, the Stratum dataLayer snippet and the
    server-side tagging configuration.

    When ``server_container_url`` is set, ``gtm.js``/``ns.html`` are loaded
    from the server container (first-party) instead of googletagmanager.com.
    """
    loader_base = server_container_url.rstrip("/") if server_container_url else GTM_PUBLIC_HOST
    container_id = (web_container_id or "").strip().upper() or None

    if container_id:
        head = _head_snippet(container_id, loader_base)
        body = _body_snippet(container_id, loader_base)
    else:
        head = "<!-- Configure a GTM web container ID (GTM-XXXXXXX) to generate this snippet -->"
        body = "<!-- Configure a GTM web container ID (GTM-XXXXXXX) to generate this snippet -->"

    sgtm_config: dict[str, Any] = {
        "server_container_url": server_container_url,
        "transport_url": server_container_url,
        "stratum_ingest_url": stratum_ingest_url,
        "source_key": source_key,
        "source_header": SOURCE_HEADER,
        "cdp_source_type": SourceType.SGTM.value,
        "meta_capi_client": META_CAPI_CLIENT_LABEL,
        "meta_pixel_id": meta_pixel_id,
        "role": SGTM_ROLE,
    }

    return GTMSnippets(
        head_snippet=head,
        body_snippet=body,
        stratum_snippet=_stratum_snippet(stratum_ingest_url, source_key),
        sgtm_config=sgtm_config,
    )


# =============================================================================
# CDP source linkage
# =============================================================================


def generate_source_key() -> str:
    """Generate a CDP source key (same convention as the CDP sources API)."""
    return f"cdp_{secrets.token_urlsafe(32)}"


async def ensure_sgtm_source(
    db: AsyncSession,
    tenant_id: int,
    web_container_id: str | None,
    server_container_url: str | None,
) -> CDPSource:
    """
    Find or create the tenant's CDP ``sgtm`` source ("Server-side GTM").

    Updates its config with the current container details when it already
    exists. The session is flushed (not committed) so the caller controls
    the transaction. Secrets are never written to ``config``.
    """
    result = await db.execute(
        select(CDPSource)
        .where(
            CDPSource.tenant_id == tenant_id,
            CDPSource.source_type == SourceType.SGTM.value,
        )
        .order_by(CDPSource.created_at)
    )
    source = result.scalars().first()

    config_update = {
        "web_container_id": web_container_id,
        "server_container_url": server_container_url,
        "role": SGTM_ROLE,
    }

    if source is None:
        source = CDPSource(
            tenant_id=tenant_id,
            name=SGTM_SOURCE_NAME,
            source_type=SourceType.SGTM.value,
            source_key=generate_source_key(),
            config=dict(config_update),
            is_active=True,
        )
        db.add(source)
        await db.flush()
        logger.info(
            "gtm_sgtm_source_created",
            tenant_id=tenant_id,
            source_id=str(source.id),
        )
    else:
        merged = dict(source.config or {})
        merged.update(config_update)
        source.config = merged  # reassign so JSONB change is tracked
        if not source.is_active:
            source.is_active = True
        await db.flush()

    return source


# =============================================================================
# Verification
# =============================================================================


def _make_http_client(timeout_seconds: float) -> httpx.AsyncClient:
    """HTTP client factory (patched in tests)."""
    return httpx.AsyncClient(
        timeout=timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": "StratumAI-GTM-Verify/1.0"},
    )


async def _check_url(client: httpx.AsyncClient, url: str) -> tuple[bool, str]:
    """GET a URL and report whether it answered 200."""
    try:
        response = await client.get(url)
    except httpx.HTTPError as exc:
        return False, f"{type(exc).__name__}"
    except Exception as exc:  # noqa: BLE001 - never raise from verification
        return False, f"{type(exc).__name__}"
    if response.status_code == 200:
        return True, "HTTP 200"
    return False, f"HTTP {response.status_code}"


async def verify_containers(
    web_container_id: str | None,
    server_container_url: str | None,
    timeout_seconds: float = 10.0,
) -> GTMVerifyResult:
    """
    Verify the web container is published (``gtm.js`` returns 200) and the
    server container answers its ``/healthy`` endpoint. Never raises.
    """
    checked_at = datetime.now(UTC)
    web_ok: bool | None = None
    server_ok: bool | None = None
    notes: list[str] = []

    container_id = (web_container_id or "").strip().upper() or None
    server_url = (server_container_url or "").strip().rstrip("/") or None

    if not container_id and not server_url:
        return GTMVerifyResult(
            success=False,
            message="No GTM containers configured",
            web_container_ok=None,
            server_container_ok=None,
            checked_at=checked_at,
        )

    try:
        async with _make_http_client(timeout_seconds) as client:
            if container_id:
                web_ok, detail = await _check_url(
                    client, f"{GTM_PUBLIC_HOST}/gtm.js?id={container_id}"
                )
                notes.append(
                    f"Web container {container_id}: {'reachable' if web_ok else 'unreachable'} ({detail})"
                )
            if server_url:
                server_ok, detail = await _check_url(client, f"{server_url}/healthy")
                notes.append(
                    f"Server container: {'healthy' if server_ok else 'unhealthy'} ({detail})"
                )
    except Exception as exc:  # noqa: BLE001 - client construction failure etc.
        logger.warning("gtm_verify_error", error=type(exc).__name__)
        notes.append(f"Verification error: {type(exc).__name__}")
        if container_id and web_ok is None:
            web_ok = False
        if server_url and server_ok is None:
            server_ok = False

    checks = [c for c in (web_ok, server_ok) if c is not None]
    success = bool(checks) and all(checks)
    return GTMVerifyResult(
        success=success,
        message="; ".join(notes) if notes else "Nothing verified",
        web_container_ok=web_ok,
        server_container_ok=server_ok,
        checked_at=checked_at,
    )


__all__ = [
    "GTM_CONTAINER_ID_RE",
    "META_CAPI_CLIENT_LABEL",
    "SGTM_ROLE",
    "SGTM_SOURCE_NAME",
    "SOURCE_HEADER",
    "GTMSnippets",
    "GTMVerifyResult",
    "build_snippets",
    "ensure_sgtm_source",
    "generate_source_key",
    "validate_container_id",
    "validate_server_container_url",
    "verify_containers",
]
