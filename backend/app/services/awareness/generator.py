from app.schemas.classification import ClassificationResult
from app.schemas.guidance import Awareness, Citation, Resource
from app.schemas.incident import IncidentData
from app.services.ai.base import LLMProvider, StructuredOutputError
from app.services.ai.prompts import awareness as prompt
from app.services.ai.structured import generate_structured
from app.services.classification.taxonomy import label
from app.services.guidance.advisor import scrub_unsupported
from app.services.guidance.safety import drop_evidence_destruction


class AwarenessGenerator:
    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    def generate(self, incident: IncidentData | None, cls: ClassificationResult | None,
                 sources: list[Citation], topic: str | None = None) -> Awareness:
        try:
            content = generate_structured(self.llm, task=prompt.TASK, system=prompt.SYSTEM,
                                          user=prompt.build(incident, cls, sources, topic), schema=Awareness)
        except StructuredOutputError:
            content = Awareness(headline=f"Staying safe from {label(cls.category) if cls else 'online scams'}")
        for field in ("warning_signs", "prevention_tips", "future_precautions"):
            items = [scrub_unsupported(x, sources) for x in getattr(content, field)]
            setattr(content, field, drop_evidence_destruction(items))
        # Resources must come from retrieved sources — drop any invented link, then fill from sources.
        allowed = {s.url: s for s in sources if s.url}
        resources: list[Resource] = []
        for r in content.resources:
            if r.url in allowed and all(x.url != r.url for x in resources):
                resources.append(r)
        for url, s in allowed.items():
            if all(r.url != url for r in resources):
                resources.append(Resource(title=s.title, organization=s.organization, url=url))
        content.resources = resources[:5]
        return content
