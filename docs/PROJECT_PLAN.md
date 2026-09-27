# Khatti API — Plan & Architecture

**Recommendation:** enter Khatti in the *Best Apps and Agents* track.

The main product is the **KYC Document Agent**: an Arabic-first reader that reads
Iraqi onboarding documents, cross-checks them, and hands the file to a human when
unsure.

Build it as a **Nemotron-orchestrated pipeline on Nebius Token Factory**, with an
**NVIDIA vision model self-hosted on a Nebius Serverless Endpoint**.

## Key finding that shapes the architecture

None of the NVIDIA vision or OCR models we could verify list Arabic as a supported
language, and Token Factory's public Nemotron endpoints are text-only as far as we
could verify. Therefore:

- **Nemotron does the reasoning, validation and routing.**
- **An ensemble of image readers, at least one of them NVIDIA, does the Arabic
  perception.**
- **Disagreement between readers becomes a confidence signal.**

## How this repo implements it

```
 images ──► Reader ensemble (parallel)          ──► Consensus          ──► Rule checks          ──► Nemotron router ──► Decision
            • NVIDIA VLM (Nebius Serverless)       per-field agreement     • Iraqi NID format       (text-only LLM,      approve /
            • Arabic-capable VLM (Token Factory)   after Arabic            • dates / expiry / age     may only escalate)   human_review /
            • … any OpenAI-compatible reader       normalisation           • passport MRZ digits                         reject
                                                                           • cross-document match
```

| Stage | Module | Notes |
|---|---|---|
| Perception | `khatti/readers/` | Each reader returns fields + its own confidence. Readers are OpenAI-compatible chat endpoints, so the NVIDIA model on a Nebius Serverless Endpoint and any Token Factory VLM share one client. |
| Normalisation | `khatti/arabic.py` | Strips diacritics/tatweel, unifies alef/ya/ta-marbuta forms, converts Eastern Arabic digits, parses dates. |
| Consensus | `khatti/consensus.py` | Similarity-weighted voting per field; agreement ratio is the confidence signal. |
| Validation | `khatti/validation.py` | Deterministic checks — no LLM needed. |
| Orchestration | `khatti/orchestrator.py` | Nemotron reviews consensus + checks and writes the rationale. Guardrail: the LLM can escalate to a human but can never downgrade a rule-based escalation to auto-approve. |
| API | `khatti/api.py` | `POST /v1/kyc/cases` (multipart). |

## Milestones

1. ✅ Pipeline skeleton, schemas, API, mock readers, tests.
2. ✅ Arabic normalisation, consensus scoring, Iraqi ID / passport MRZ checks.
3. ✅ Nemotron router with guardrails and deterministic fallback.
4. ☐ Deploy NVIDIA VLM on a Nebius Serverless Endpoint; wire its URL into config.
5. ☐ Collect a small labelled set of (synthetic) Iraqi documents; tune thresholds.
6. ☐ Reviewer UI for the human-review queue; demo video for submission.
