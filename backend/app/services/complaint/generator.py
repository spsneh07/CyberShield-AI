"""Formal complaint draft: deterministic template + LLM-written narrative constrained to facts."""
import re
from datetime import date

from app.schemas.classification import ClassificationResult
from app.schemas.guidance import ComplaintNarrative
from app.schemas.incident import ComplainantDetails, IncidentData
from app.services.ai.base import LLMProvider, StructuredOutputError
from app.services.ai.prompts import complaint as prompt
from app.services.ai.structured import generate_structured
from app.services.classification.taxonomy import label
from app.services.complaint.validator import validate_narrative
from app.services.evidence.manifest import EvidenceFile, annexure
from app.services.incident.identifiers import extract_identifiers, normalize

PLACEHOLDER_RE = re.compile(r"\[[A-Z][A-Z0-9 /'’&,.-]{1,80}\]")

DECLARATION = (
    "I hereby declare that the information given above is true and correct to the best of my "
    "knowledge and belief. I am willing to provide any further information or evidence required."
)


def _val(value: object, placeholder: str) -> str:
    if value in (None, "", []):
        return f"[{placeholder}]"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _grounded_narrative(narrative: str, incident: IncidentData) -> str:
    """Replace identifiers in the narrative that the user never provided with placeholders."""
    known = {normalize(v) for f in ("phone_numbers", "emails", "urls", "upi_ids", "account_identifiers") for v in getattr(incident, f)}
    found = extract_identifiers(narrative)
    for field, ph in (("phone_numbers", "PHONE NUMBER"), ("emails", "EMAIL"), ("urls", "URL"),
                      ("upi_ids", "UPI ID"), ("account_identifiers", "TRANSACTION ID")):
        for v in getattr(found, field):
            if normalize(v) not in known:
                narrative = narrative.replace(v, f"[{ph}]")
    return narrative


class ComplaintGenerator:
    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    def generate(self, incident: IncidentData, cls: ClassificationResult | None,
                 complainant: ComplainantDetails | None = None,
                 evidence_files: list[EvidenceFile] | None = None) -> tuple[str, str, list[str], list[str]]:
        """Return (subject, body, placeholders, validation_notes)."""
        c = complainant or ComplainantDetails()
        category = label(cls.category) if cls and cls.category != "unknown" else (incident.incident_type or "[INCIDENT TYPE]")
        try:
            out = generate_structured(self.llm, task=prompt.TASK, system=prompt.SYSTEM,
                                      user=prompt.build(incident, cls), schema=ComplaintNarrative)
            narrative = _grounded_narrative(out.narrative.strip(), incident)
            narrative, notes = validate_narrative(narrative, incident)
            requests = out.requested_assistance
        except StructuredOutputError:
            narrative = incident.description or "[DESCRIBE THE INCIDENT IN DETAIL]"
            requests, notes = [], []
        if not requests:
            requests = ["Register my complaint and investigate the matter."]

        if incident.amount is not None and incident.financial_loss is not False:
            loss = f"{incident.currency or '[CURRENCY]'} {incident.amount:,.2f}"
        elif incident.financial_loss is False:
            loss = "No financial loss reported."
        else:
            loss = "[AMOUNT LOST, IF ANY]"

        if incident.financial_loss is False:
            requests = [r for r in requests if not re.search(r"(trace and freeze|recovery of funds|recover transferred|recover.*money)", r, re.I)]
            if not requests:
                requests = ["Register my complaint and investigate the matter."]

        subject = f"Complaint regarding {category} incident" + (f" via {incident.platform}" if incident.platform else "")
        evidence_lines = list(incident.evidence_available)
        if evidence_files:
            evidence_lines.append(f"{len(evidence_files)} digital file(s) with SHA-256 fingerprints, listed in Annexure A")
        evidence = "\n".join(f"  {i}. {e}" for i, e in enumerate(evidence_lines, 1)) or "  1. [LIST OF EVIDENCE, E.G. SCREENSHOTS, SMS, BANK STATEMENT]"
        actions = "\n".join(f"  - {a}" for a in incident.actions_taken) or "  - [ACTIONS ALREADY TAKEN, E.G. BANK INFORMED ON DATE, REFERENCE NO.]"
        req = "\n".join(f"  {i}. {r}" for i, r in enumerate(requests, 1))

        body = f"""To,
The Officer In-Charge,
[CYBER CRIME CELL / POLICE STATION NAME]
[CITY, STATE]

Date: {date.today().strftime('%d %B %Y')}

Subject: {subject}

Respected Sir/Madam,

1. Complainant details
  Name: {_val(c.name, 'FULL NAME')}
  Contact: {_val(c.contact, 'MOBILE NUMBER / EMAIL')}
  Address: {_val(c.address, 'ADDRESS')}

2. Incident details
  Type of incident: {category}
  Date and time of incident: {_val(incident.date_time, 'DATE AND TIME')}
  Platform / medium: {_val(incident.platform, 'PLATFORM / MEDIUM')}

3. Description of the incident
{narrative}

4. Suspect information
  Phone number(s): {_val(incident.phone_numbers, 'SUSPECT PHONE NUMBER, IF KNOWN')}
  Email(s): {_val(incident.emails, 'SUSPECT EMAIL, IF KNOWN')}
  Website / link(s): {_val(incident.urls, 'SUSPICIOUS URL, IF ANY')}
  UPI ID(s): {_val(incident.upi_ids, 'SUSPECT UPI ID, IF ANY')}

5. Financial loss and transaction details
  Amount lost: {loss}
  Transaction / reference ID(s): {_val(incident.account_identifiers, 'TRANSACTION ID / UTR')}
  Bank / payment app: [BANK OR PAYMENT APP NAME]

6. Evidence available
{evidence}

7. Actions already taken
{actions}

8. Assistance requested
{req}

{DECLARATION}

Yours faithfully,

{_val(c.name, 'FULL NAME')}
[SIGNATURE]
"""
        if evidence_files:
            body += "\n" + annexure(evidence_files) + "\n"
        placeholders = sorted(set(PLACEHOLDER_RE.findall(body)))
        return subject, body, placeholders, notes
