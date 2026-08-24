import html
import re
import warnings
from typing import List, Set, Optional
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from worker.scraper.rules import RuleConfig, is_excluded_url, score_url


warnings.filterwarnings(
    "ignore",
    category=XMLParsedAsHTMLWarning,
)


class DiscoveryEngine:
    def __init__(self, config: Optional[RuleConfig] = None):
        self.config = config or RuleConfig()

        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": (
                '"Chromium";v="122", '
                '"Not(A:Brand";v="24", '
                '"Google Chrome";v="122"'
            ),
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }

    def normalize_url(self, url: str) -> str:
        url = url.strip()

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        parsed = urlparse(url)

        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    def fetch_robots_sitemaps(self, base_url: str) -> List[str]:
        sitemaps = set()

        robots_url = f"{base_url}/robots.txt"

        try:
            resp = httpx.get(
                robots_url,
                headers=self.headers,
                timeout=10.0,
                follow_redirects=True,
            )

            if resp.status_code == 200:
                for line in resp.text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        sitemap_url = line.split(":", 1)[1].strip()
                        sitemaps.add(sitemap_url)

        except Exception:
            pass

        return list(sitemaps)

    def discover_sitemaps(self, base_url: str) -> List[str]:
        sitemaps = set(
            self.fetch_robots_sitemaps(base_url)
        )

        common_locations = [
            "/sitemap.xml",
            "/sitemap_index.xml",
            "/wp-sitemap.xml",
            "/sitemap-index.xml",
        ]

        for loc in common_locations:
            target = base_url + loc

            if target not in sitemaps:
                try:
                    resp = httpx.head(
                        target,
                        headers=self.headers,
                        timeout=5.0,
                        follow_redirects=True,
                    )

                    if resp.status_code == 200:
                        sitemaps.add(target)

                except Exception:
                    pass

        return list(sitemaps)

    def parse_sitemap_urls(
        self,
        sitemap_url: str,
        visited_sitemaps: Optional[Set[str]] = None,
    ) -> List[str]:

        if visited_sitemaps is None:
            visited_sitemaps = set()

        if sitemap_url in visited_sitemaps:
            return []

        visited_sitemaps.add(sitemap_url)

        urls = []

        try:
            content = None

            try:
                resp = httpx.get(
                    sitemap_url,
                    headers=self.headers,
                    timeout=12.0,
                    follow_redirects=True,
                )

                if (
                    resp.status_code == 200
                    and "Just a moment..." not in resp.text
                ):
                    content = resp.text

            except Exception:
                pass

            # Cloudflare / WAF fallback
            if not content:
                print(
                    f"  [DEBUG] httpx blocked/failed on "
                    f"{sitemap_url}, using Trafilatura fetch fallback..."
                )

                content = trafilatura.fetch_url(
                    sitemap_url
                )

            if not content:
                return []

            # Try XML <loc> extraction first
            raw_loc_matches = re.findall(
                r"<loc>(.*?)</loc>",
                content,
                re.IGNORECASE | re.DOTALL,
            )

            loc_matches = [
                html.unescape(match.strip())
                for match in raw_loc_matches
                if match.strip()
            ]

            # HTML fallback
            if not loc_matches:
                soup = BeautifulSoup(
                    content,
                    "html.parser",
                )

                a_tags = soup.find_all(
                    "a",
                    href=True,
                )

                for a in a_tags:
                    href = a["href"].strip()

                    if href.startswith("http") or href.startswith("/"):
                        full_url = urljoin(
                            sitemap_url,
                            href,
                        )

                        loc_matches.append(full_url)

            print(
                f"  [DEBUG] Fetching sitemap: {sitemap_url} "
                f"-> Found {len(loc_matches)} URL match(es)"
            )

            for loc_str in loc_matches:
                parsed_loc = urlparse(loc_str)
                base_netloc = urlparse(
                    sitemap_url
                ).netloc

                # Only follow URLs belonging to the same domain
                if (
                    parsed_loc.netloc
                    and parsed_loc.netloc != base_netloc
                ):
                    continue

                is_sub_sitemap = (
                    loc_str.endswith(".xml")
                    or "-sitemap" in loc_str.lower()
                    or "sitemap_" in loc_str.lower()
                )

                # Ignore asset/content sitemaps
                loc_lower = loc_str.lower()

                if any(
                    ignored in loc_lower
                    for ignored in [
                        "image-sitemap",
                        "video-sitemap",
                        "author-sitemap",
                        "tag-sitemap",
                        "user-sitemap",
                    ]
                ):
                    continue

                if (
                    is_sub_sitemap
                    and loc_str != sitemap_url
                ):
                    if loc_str not in visited_sitemaps:
                        sub_urls = self.parse_sitemap_urls(
                            loc_str,
                            visited_sitemaps,
                        )

                        urls.extend(sub_urls)

                else:
                    urls.append(loc_str)

        except Exception as exc:
            print(
                f"  [DEBUG] Error fetching "
                f"{sitemap_url}: {exc}"
            )

        return list(set(urls))

    def discover_hub_pages(
        self,
        base_url: str,
    ) -> List[str]:
        """
        Discover listing/hub pages such as:
        /blog/
        /blogs/
        /insights/
        /insights/blog/
        """

        found_hubs = []

        for path in self.config.common_hub_paths:
            hub_url = urljoin(
                base_url,
                path,
            )

            try:
                resp = httpx.get(
                    hub_url,
                    headers=self.headers,
                    timeout=6.0,
                    follow_redirects=True,
                )

                if resp.status_code == 200:
                    found_hubs.append(
                        str(resp.url)
                    )

            except Exception:
                pass

        return list(set(found_hubs))

    def crawl_hub_for_links(
        self,
        hub_url: str,
        base_url: str,
    ) -> List[str]:

        links = set()

        base_netloc = urlparse(
            base_url
        ).netloc

        try:
            resp = httpx.get(
                hub_url,
                headers=self.headers,
                timeout=10.0,
                follow_redirects=True,
            )

            if resp.status_code == 200:
                soup = BeautifulSoup(
                    resp.text,
                    "html.parser",
                )

                for a_tag in soup.find_all(
                    "a",
                    href=True,
                ):
                    href = a_tag["href"].strip()

                    full_url = urljoin(
                        hub_url,
                        href,
                    )

                    parsed_full = urlparse(
                        full_url
                    )

                    # Internal links only
                    if parsed_full.netloc == base_netloc:
                        clean_url = (
                            f"{parsed_full.scheme}://"
                            f"{parsed_full.netloc}"
                            f"{parsed_full.path}"
                        )

                        if (
                            clean_url
                            and clean_url != base_url
                            and clean_url != hub_url
                        ):
                            links.add(
                                clean_url.rstrip("/")
                            )

        except Exception:
            pass

        return list(links)

    def filter_candidate_urls(
        self,
        raw_urls: List[str],
    ) -> List[str]:

        unique_urls = set(raw_urls)
        accepted = []

        for url in unique_urls:
            if is_excluded_url(
                url,
                self.config,
            ):
                continue

            score = score_url(
                url,
                self.config,
            )

            if score > 0:
                accepted.append(url)

        return accepted