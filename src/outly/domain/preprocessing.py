import re
from dataclasses import dataclass
from urllib.parse import quote

LINK_PATTERN = re.compile(
    r"<a\s([^>]*?)href\s*=\s*[\"']([^\"']+)[\"']([^>]*)>", re.IGNORECASE
)


@dataclass(frozen=True)
class PreprocessOptions:
    email_job_id: str
    tracking_base_url: str
    track_opens: bool
    track_clicks: bool


def rewrite_links(html: str, email_job_id: str, tracking_base_url: str) -> str:
    def replace(match: re.Match[str]) -> str:
        before, url, after = match.groups()
        if url.startswith("mailto:") or url.startswith("#") or "/track/click/" in url:
            return match.group(0)
        tracked = f"{tracking_base_url}/track/click/{email_job_id}?url={quote(url, safe='')}"
        return f'<a {before}href="{tracked}"{after}>'

    return LINK_PATTERN.sub(replace, html)


def inject_tracking_pixel(html: str, email_job_id: str, tracking_base_url: str) -> str:
    pixel = (
        f'<img src="{tracking_base_url}/track/open/{email_job_id}" '
        'width="1" height="1" style="display:none;" alt="" />'
    )
    if "</body>" in html:
        return html.replace("</body>", f"{pixel}</body>", 1)
    return html + pixel


def preprocess_email_html(html: str, options: PreprocessOptions) -> str:
    result = html
    if options.track_clicks:
        result = rewrite_links(result, options.email_job_id, options.tracking_base_url)
    if options.track_opens:
        result = inject_tracking_pixel(result, options.email_job_id, options.tracking_base_url)
    return result


def strip_html(html: str) -> str:
    return re.sub(r"<[^>]*>?", "", html)
