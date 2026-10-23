"""Conversation flow: message -> intent -> extraction -> validation -> classification ->
retrieval -> generation -> persistence. Each stage is reported through `emit`."""
import logging

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.repositories.conversations import ConversationRepository
from app.schemas.conversation import AssistantResult, ChatReply, IntentResult, MessageIn
from app.schemas.guidance import Citation
from app.services.ai.base import LLMError, StructuredOutputError
from app.services.ai.prompts import conversation as prompt
from app.services.ai.structured import generate_structured
from app.services.guidance.advisor import known_text, scrub_unsupported
from app.services.guidance.safety import scrub_reply
from app.services.incident.extractor import follow_up_questions
from app.services.language import current_language, set_language
from app.services.scamcheck.red_flags import detect_red_flags
from app.services.pipeline import Pipeline, StageCallback, _noop

log = logging.getLogger(__name__)

# Fixed replies used when the model is unavailable, per response language.
FALLBACK_REPLIES = {
    "en": {"analysed": "I've analysed your incident — see the summary, recommended actions and sources below.",
           "failed": "Sorry, I couldn't generate a reply just now. Please try again."},
    "hi": {"analysed": "मैंने आपकी घटना का विश्लेषण कर लिया है — सारांश, सुझाए गए कदम और स्रोत नीचे देखें।",
           "failed": "माफ़ कीजिए, अभी जवाब तैयार नहीं हो सका। कृपया फिर से कोशिश करें।"},
}


class Orchestrator:
    def __init__(self, db: Session, pipeline: Pipeline) -> None:
        self.convs = ConversationRepository(db)
        self.p = pipeline
        self.llm = pipeline.llm

    def handle(self, conversation_id: str, msg: MessageIn, emit: StageCallback = _noop) -> AssistantResult:
        conv = self.convs.get(conversation_id)
        if conv is None:
            raise NotFoundError("Conversation")
        set_language(msg.language)
        history = self.convs.list_messages(conversation_id, limit=6)
        self.convs.add_message(conversation_id, "user", msg.content, intent=msg.action)
        incident = self.p.incidents.latest_for_conversation(conversation_id)

        emit("analyzing")
        intent = msg.action or self._intent(msg.content, incident is not None, history)
        if intent == "provide_details" and incident is None:
            intent = "report_incident"
        # "...for the complaint" in a first, detailed report: analyse it instead of asking them to describe it again.
        if intent == "generate_complaint" and incident is None and msg.action is None and len(msg.content.split()) >= 12:
            intent = "report_incident"
        stages = ["analyzing"]

        def track(stage: str) -> None:
            if stage in stages:  # each stage is reported once, in order
                return
            stages.append(stage)
            emit(stage)

        result = AssistantResult(intent=intent, reply="", provider=self.llm.name, language=msg.language)

        if intent in ("report_incident", "provide_details", "check_message"):
            existing = incident if intent == "provide_details" else None
            out = self.p.analyze(msg.content, conversation_id, existing, emit=track)
            incident = out["incident"]
            result.incident_id = incident.id
            result.incident, result.classification = out["data"], out["classification"]
            result.explanation, result.guidance, result.sources = out["explanation"], out["guidance"], out["sources"]
            result.warnings = out["warnings"]
            result.follow_up_questions = follow_up_questions(out["data"].missing_information)
            if intent != "provide_details":  # rule-based highlighting of the pasted/described text
                result.red_flags = detect_red_flags(msg.content)
            self.convs.set_title_if_default(conv, out["data"].description[:60])

        elif intent in ("generate_complaint", "evidence_checklist") and incident is None:
            result.reply = ("I don't have an incident to work with yet. Please describe what happened — for example, "
                            "how you were contacted, what was asked of you, and whether any money was lost.")

        elif intent == "generate_complaint":
            track("generating")
            result.incident_id = incident.id
            result.incident, result.classification = self.p.load(incident)
            result.complaint = self.p.complaint_for(incident, msg.complainant, msg.evidence_files)
            result.reply = (f"I've prepared an editable complaint draft. Fill in the {len(result.complaint.placeholders)} "
                            "bracketed placeholders before submitting — I have not guessed any of them.")

        elif intent == "evidence_checklist":
            track("retrieving")
            result.incident_id = incident.id
            result.incident, result.classification = self.p.load(incident)
            result.explanation, result.guidance, result.sources = self.p.guidance_for(incident)
            result.reply = "Here is the evidence checklist for your incident. Keep originals — never edit or delete them."

        elif intent == "safety_tips":
            track("retrieving")
            result.awareness, result.sources = self.p.awareness_for(incident, topic=None if incident else msg.content)
            if incident:
                result.incident_id = incident.id

        else:  # question / smalltalk
            if intent == "question":
                track("retrieving")
                result.sources = self.p.retriever.search(msg.content)

        if not result.reply:
            track("generating")
            known = known_text(result.incident) if result.incident else ""
            result.reply = scrub_unsupported(scrub_reply(self._reply(msg.content, result)), result.sources, known)

        track("saving")
        result.stages = stages
        self.convs.add_message(conversation_id, "assistant", result.reply, intent=intent,
                               payload=result.model_dump(mode="json", exclude={"reply"}))
        return result

    def _intent(self, message: str, has_incident: bool, history: list) -> str:
        recent = [{"role": m.role, "content": m.content[:300]} for m in history[-4:]]
        try:
            return generate_structured(self.llm, task=prompt.INTENT_TASK, system=prompt.INTENT_SYSTEM,
                                       user=prompt.build_intent(message, has_incident, recent), schema=IntentResult).intent
        except StructuredOutputError:
            return "provide_details" if has_incident else "report_incident"

    def _reply(self, message: str, r: AssistantResult) -> str:
        ctx: dict = {"follow_up_questions": r.follow_up_questions}
        if r.classification:
            ctx["classification"] = r.classification.model_dump()
            ctx["incident"] = r.incident.model_dump(mode="json", include={"platform", "financial_loss", "amount", "date_time"}) if r.incident else None
            ctx["top_actions"] = r.guidance.immediate_actions[:3] if r.guidance else []
        if r.awareness:
            ctx["awareness_headline"] = r.awareness.headline
        sources: list[Citation] = r.sources
        try:
            return generate_structured(self.llm, task=prompt.REPLY_TASK, system=prompt.REPLY_SYSTEM,
                                       user=prompt.build_reply(message, r.intent, ctx, sources), schema=ChatReply).reply
        except (StructuredOutputError, LLMError):
            replies = FALLBACK_REPLIES.get(current_language(), FALLBACK_REPLIES["en"])
            return replies["analysed"] if r.classification else replies["failed"]
