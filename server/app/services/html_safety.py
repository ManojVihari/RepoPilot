"""
HTML that reaches a page from documentation is untrusted: docs are built from
repository code (comments, annotations, strings) and LLM output. Everything
rendered from markdown goes through `clean_html` (an allowlist, via nh3) and
pages carry a nonce-based Content-Security-Policy as a second line of defence.
"""
import secrets

import markupsafe
import nh3

# what markdown (tables, fenced code, toc) produces, plus the diff highlights
ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "code", "del", "div", "em", "h1", "h2", "h3", "h4", "h5", "h6",
    "hr", "i", "ins", "kbd", "li", "ol", "p", "pre", "s", "span", "strong", "sub", "sup",
    "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}
ALLOWED_ATTRIBUTES = {
    "*": {"class", "title"},
    "a": {"href"},
    "h1": {"id"}, "h2": {"id"}, "h3": {"id"}, "h4": {"id"}, "h5": {"id"}, "h6": {"id"},
    "td": {"colspan", "rowspan", "align"}, "th": {"colspan", "rowspan", "align", "scope"},
    "ol": {"start"},
}
URL_SCHEMES = {"http", "https", "mailto"}


def clean_html(html) -> markupsafe.Markup:
    """Untrusted HTML -> markup safe to put in a page (scripts, handlers, javascript: URLs removed)."""
    if not html:
        return markupsafe.Markup("")
    cleaned = nh3.clean(
        str(html),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=URL_SCHEMES,
        link_rel="noopener noreferrer nofollow",
    )
    return markupsafe.Markup(cleaned)


# ---------------------------------------------------------------- headers

def new_nonce() -> str:
    return secrets.token_urlsafe(18)


def content_security_policy(nonce: str) -> str:
    return "; ".join([
        "default-src 'self'",
        f"script-src 'self' 'nonce-{nonce}'",
        "style-src 'self' 'unsafe-inline'",       # style attributes only; no remote stylesheets
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}
