import httpx
import trafilatura

from worker.scraper.models import ExtractionResult


class ContentExtractor:
    def __init__(self):
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }

    def extract_trafilatura(
        self,
        html: str,
        url: str,
    ) -> ExtractionResult:

        result = ExtractionResult(
            url=url,
            extractor_name="Trafilatura",
        )

        try:
            extracted_text = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                output_format="txt",
            )

            metadata = trafilatura.extract_metadata(
                html
            )

            if extracted_text:
                result.text = extracted_text
                result.content_length = len(
                    extracted_text
                )

                result.content_preview = (
                    extracted_text[:200] + "..."
                    if len(extracted_text) > 200
                    else extracted_text
                )

            if metadata:
                result.title = metadata.title
                result.author = metadata.author
                result.date = metadata.date

        except Exception as exc:
            result.extra_metadata["error"] = str(exc)

        return result

    def extract_all(self, url: str) -> dict:
        results = {}

        try:
            html = None

            try:
                resp = httpx.get(
                    url,
                    headers=self.headers,
                    timeout=10.0,
                    follow_redirects=True,
                )

                if (
                    resp.status_code == 200
                    and "Just a moment..." not in resp.text
                ):
                    html = resp.text

            except Exception:
                pass

            # WAF / Cloudflare fallback
            if not html:
                html = trafilatura.fetch_url(url)

            if html:
                results["trafilatura"] = (
                    self.extract_trafilatura(
                        html,
                        url,
                    )
                )

        except Exception as exc:
            results["error"] = str(exc)

        return results