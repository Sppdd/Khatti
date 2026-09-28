# Feedback log (Nebius / NVIDIA)

A running log for the hackathon feedback form. Add entries as they happen; date each one.

| Date | Area | What happened | Impact | Suggestion |
|---|---|---|---|---|
| 2026-09-28 | Token Factory models | No public image-input Nemotron: builders report Nano/Super return 400 "does not support image input", while the document-intelligence page mentions Nemotron Nano 2 VL. | Had to self-host Nano Omni on a Serverless Endpoint for the NVIDIA reader. | Make the vision catalogue explicit per key (`GET /v1/models?verbose=true` with an input-modalities field). |
| 2026-09-28 | Token Factory API | JSON mode / `json_schema` / `logprobs` support varies per model and is not visible programmatically. | Every endpoint needs a `json_mode` switch and a logprob-free confidence path. | Expose capabilities per model in the models endpoint. |
| 2026-09-28 | Arabic | No NVIDIA vision/OCR model we could verify lists Arabic (Omni card: English only; OCR v2 and Nano 2 VL omit Arabic). | Two-reader ensemble; Nemotron limited to reasoning. | Publish Arabic evals, even if weak; state it in model cards. |
| 2026-09-28 | Sandboxes | Beta terms forbid personal or sensitive data. | Sandboxes used only with synthetic fixtures (validator codegen). | A PII-safe tier or clearer data-handling statement. |
| 2026-09-28 | Terraform | Provider registry mirror (`terraform-provider.storage.eu-north1.nebius.cloud`) and Serverless Endpoint/Job coverage were hard to confirm. | Serverless deploys scripted with the CLI. | Document Serverless resources in the provider, with examples. |
| 2026-09-28 | NeMo Agent Toolkit 1.9.0 | `nat eval` from `nvidia-nat-eval[full]` fails with `ModuleNotFoundError: langchain_core` (imported by the intermediate-step adapter) and needs `nvidia-nat-profiler` for `profiler:` configs; neither is declared. | Two extra installs to get `nat eval` + profiler running on a non-LangChain workflow. | Declare `langchain-core` in `nvidia-nat-eval[full]`, or make the import lazy; point to the profiler extra in the error. |
| 2026-09-28 | Nebius Python SDK | `EndpointSpec.args` / `container_command` are single strings, not lists, unlike most container APIs. | Caught by the SDK's type check before any call. | Document the quoting rules for `args`. |
| | Serverless Endpoints | _(record cold-start times for the Omni endpoint here)_ | | |
