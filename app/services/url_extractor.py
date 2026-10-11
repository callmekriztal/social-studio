import ipaddress
import socket
import urllib.parse
from dataclasses import dataclass
from typing import Optional, Set
import httpx
from bs4 import BeautifulSoup


class SSRError(Exception):
    """Raised when an SSRF attempt or forbidden destination is detected."""
    pass


class ExtractionError(Exception):
    """Raised when URL content cannot be fetched or parsed."""
    pass


@dataclass
class ExtractedArticle:
    """Extracted article title, clean content, and source URL."""

    title: str
    content: str
    source_url: str


class URLExtractorService:
    """Service for securely fetching and extracting blog post text from URLs."""

    MAX_RESPONSE_SIZE = 2 * 1024 * 1024  # 2 MB
    REQUEST_TIMEOUT = 10.0  # 10 seconds
    MAX_REDIRECTS = 5

    @classmethod
    def is_ip_allowed(cls, ip_str: str) -> bool:
        """Verify an IP address is a public, non-reserved address."""
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False

        # Additional protection for carrier-grade NAT (100.64.0.0/10)
        if isinstance(ip, ipaddress.IPv4Address):
            cgnat_net = ipaddress.IPv4Network("100.64.0.0/10")
            if ip in cgnat_net:
                return False

        return True

    @classmethod
    def validate_url_safety(cls, url: str) -> urllib.parse.SplitResult:
        """Validate URL scheme and resolve DNS to ensure no internal network destinations."""
        parsed = urllib.parse.urlsplit(url)

        if parsed.scheme.lower() not in ("http", "https"):
            raise SSRError(f"Unsupported URL scheme '{parsed.scheme}'. Only http and https are allowed.")

        hostname = parsed.hostname
        if not hostname:
            raise SSRError("Invalid URL: missing hostname.")

        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)

        # First check if hostname is a direct IP address literal
        try:
            ip_literal = ipaddress.ip_address(hostname)
            if not cls.is_ip_allowed(str(ip_literal)):
                raise SSRError(f"Access to private/internal IP '{hostname}' is forbidden.")
            return parsed
        except ValueError:
            pass

        # Resolve DNS
        try:
            addr_info = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise ExtractionError(f"Failed to resolve host '{hostname}': {e}")

        if not addr_info:
            raise ExtractionError(f"Could not resolve any IP address for host '{hostname}'.")

        # Check ALL resolved IP addresses
        resolved_ips: Set[str] = set()
        for family, socktype, proto, canonname, sockaddr in addr_info:
            ip_addr = sockaddr[0]
            resolved_ips.add(ip_addr)
            if not cls.is_ip_allowed(ip_addr):
                raise SSRError(
                    f"Host '{hostname}' resolved to forbidden/private address '{ip_addr}'."
                )

        return parsed

    @classmethod
    def fetch_url_content(cls, initial_url: str) -> tuple[str, str]:
        """Fetch URL with SSRF checks on every hop and strict response limits.

        Returns:
            Tuple of (final_url, response_text).
        """
        current_url = initial_url
        headers = {
            "User-Agent": "SocialMediaStudioBot/1.0 (+https://github.com/social-media-studio)",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
        }

        with httpx.Client(timeout=cls.REQUEST_TIMEOUT, follow_redirects=False) as client:
            for _ in range(cls.MAX_REDIRECTS + 1):
                # Validate current URL before sending request (prevents SSRF via redirects)
                cls.validate_url_safety(current_url)

                try:
                    with client.stream("GET", current_url, headers=headers) as response:
                        if response.is_redirect:
                            location = response.headers.get("Location")
                            if not location:
                                raise ExtractionError("Redirect received with no Location header.")
                            # Handle relative redirects
                            current_url = urllib.parse.urljoin(current_url, location)
                            continue

                        if response.status_code >= 400:
                            raise ExtractionError(
                                f"Remote server returned HTTP error status {response.status_code}."
                            )

                        content_type = response.headers.get("content-type", "").lower()
                        if not any(t in content_type for t in ("text/html", "application/xhtml+xml", "text/plain")):
                            raise ExtractionError(
                                f"Unsupported content type '{content_type}'. Expected text/html or text/plain."
                            )

                        content_bytes = bytearray()
                        for chunk in response.iter_bytes():
                            content_bytes.extend(chunk)
                            if len(content_bytes) > cls.MAX_RESPONSE_SIZE:
                                raise ExtractionError(
                                    f"Response exceeded maximum allowed size of {cls.MAX_RESPONSE_SIZE} bytes."
                                )

                        encoding = response.encoding or "utf-8"
                        text = content_bytes.decode(encoding, errors="replace")
                        return current_url, text

                except httpx.TimeoutException:
                    raise ExtractionError(f"Request to '{current_url}' timed out.")
                except httpx.RequestError as e:
                    raise ExtractionError(f"Network error while connecting to '{current_url}': {e}")

            raise ExtractionError(f"Too many redirects (exceeded limit of {cls.MAX_REDIRECTS}).")

    @classmethod
    def extract_article(cls, url: str) -> ExtractedArticle:
        """Fetch and extract article title and readable text content."""
        final_url, raw_content = cls.fetch_url_content(url)

        soup = BeautifulSoup(raw_content, "html.parser")

        # Extract title
        title: Optional[str] = None
        # Try <meta property="og:title">
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = og_title["content"].strip()

        # Fallback to <title> tag
        if not title and soup.title and soup.title.string:
            title = soup.title.string.strip()

        # Fallback to <h1>
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(strip=True)

        if not title:
            title = "Extracted Article"

        # Remove irrelevant elements
        for element in soup(["script", "style", "nav", "footer", "header", "noscript", "svg", "form"]):
            element.decompose()

        # Target article or main container if present
        container = soup.find("article") or soup.find("main") or soup.body or soup

        # Extract text paragraphs
        paragraphs = [p.get_text(strip=True) for p in container.find_all("p")]
        clean_text = "\n\n".join(p for p in paragraphs if p)

        # Fallback if no <p> tags found
        if not clean_text:
            clean_text = container.get_text(separator="\n", strip=True)

        if not clean_text or len(clean_text.strip()) == 0:
            raise ExtractionError("Could not extract readable article text from the URL.")

        return ExtractedArticle(
            title=title,
            content=clean_text,
            source_url=final_url,
        )
