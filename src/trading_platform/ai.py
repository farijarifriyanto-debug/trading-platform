from dataclasses import dataclass


@dataclass(frozen=True)
class ResearchSuggestion:
    thesis: str
    confidence: float
    evidence: list[str]


class AIResearchBoundary:
    """AI may suggest research hypotheses, never submit orders directly."""

    def suggest(self, market_context: str) -> ResearchSuggestion:
        if not market_context.strip():
            return ResearchSuggestion("Insufficient context", 0.0, [])
        return ResearchSuggestion(
            thesis="Research-only placeholder; connect an approved model adapter here.",
            confidence=0.0,
            evidence=[],
        )
