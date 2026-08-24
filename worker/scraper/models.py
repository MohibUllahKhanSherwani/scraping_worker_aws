from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


@dataclass
class ExtractionResult:
    url: str
    extractor_name: str
    title: Optional[str] = None
    author: Optional[str] = None
    date: Optional[str] = None
    text: Optional[str] = None
    content_length: int = 0
    content_preview: Optional[str] = None
    extra_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WebsiteReport:
    website_url: str
    discovered_sitemaps: List[str] = field(default_factory=list)
    total_candidate_urls: int = 0
    accepted_article_urls: int = 0
    parsed_trafilatura_count: int = 0
    article_results: List[Dict[str, Any]] = field(default_factory=list)