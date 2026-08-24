import re
from dataclasses import dataclass, field
from typing import List


@dataclass
class RuleConfig:
    # URL path segments that strongly suggest blog/article content
    candidate_keywords: List[str] = field(default_factory=lambda: [
        "blog",
        "blogs",
        "insight",
        "insights",
        "news",
        "article",
        "articles",
        "resource",
        "resources",
        "story",
        "stories",
        "press-release",
        "case-study",
    ])

    # Common listing/hub entry points to probe and crawl fallback links
    common_hub_paths: List[str] = field(default_factory=lambda: [
        "/blog/",
        "/blogs/",
        "/insights/",
        "/insights/blog/",
        "/news/",
        "/articles/",
        "/resources/",
    ])

    # Path segments or extensions that indicate non-article pages
    exclusion_keywords: List[str] = field(default_factory=lambda: [
        "category",
        "categories",
        "tag",
        "tags",
        "author",
        "archive",
        "archives",
        "page",
        "search",
        "login",
        "signup",
        "register",
        "cart",
        "checkout",
        "contact",
        "about",
        "careers",
        "privacy",
        "terms",
        "faq",
        "help",
        "wp-content",
        "wp-includes",
        "assets",
        "static",
        "cdn-cgi",
        "services",
        "solutions",
    ])

    # Non-HTML file extensions to ignore
    ignored_extensions: List[str] = field(default_factory=lambda: [
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".webp",
        ".pdf",
        ".zip",
        ".css",
        ".js",
        ".json",
        ".xml",
        ".mp4",
        ".mp3",
    ])


def is_excluded_url(url: str, config: RuleConfig) -> bool:
    url_lower = url.lower()

    # Check ignored extensions
    if any(
        url_lower.endswith(ext) or f"{ext}?" in url_lower
        for ext in config.ignored_extensions
    ):
        return True

    # Check path exclusion keywords
    for keyword in config.exclusion_keywords:
        pattern = rf"[/\?=-]{re.escape(keyword)}([/\?=-]|$)"
        if re.search(pattern, url_lower):
            return True

    return False


def score_url(url: str, config: RuleConfig) -> int:
    """
    Score candidate URL deterministically.
    Higher score indicates a higher likelihood of being an article.
    """
    score = 0
    url_lower = url.lower()

    # Bonus for explicit candidate path keywords
    for keyword in config.candidate_keywords:
        pattern = rf"/{re.escape(keyword)}(/|$)"
        if re.search(pattern, url_lower):
            score += 3
            break

    # Check path depth and slug structure
    path = url.split("://")[-1].split("/", 1)[-1].strip("/")
    parts = [p for p in path.split("/") if p]

    if parts:
        last_part = parts[-1]

        # Slug detector: contains hyphens and no file extension
        hyphen_count = last_part.count("-")

        if (
            hyphen_count >= 2
            and not any(
                last_part.endswith(ext)
                for ext in config.ignored_extensions
            )
        ):
            score += 2

    return score