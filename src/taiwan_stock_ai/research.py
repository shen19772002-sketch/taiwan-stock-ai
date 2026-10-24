"""Extension point for future opt-in news/LLM research; no API calls in V1."""

from typing import Protocol


class ResearchEnricher(Protocol):
    def enrich(self, verified_report: dict) -> dict:
        """Return cited commentary without mutating prices, scores or forecasts.

        Implementations must preserve source URLs and publication timestamps;
        use GitHub Secrets/environment bindings for credentials and never emit
        key values into reports. Deep research is not enabled in V1.
        """
        ...
