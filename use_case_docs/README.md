# Assignment Package — Incident and Ticket Enrichment Copilot

Welcome. This folder is your complete, self-contained assignment package for the
**Senior Software Engineer – Copilot Integration** assignment. Your assigned use case is
**Incident and Ticket Enrichment Copilot**.

## What is in this folder

| File / folder | Description |
| --- | --- |
| `Assignment_Use_Case.md` | The full assignment brief. Contains the objective, the mandatory technical scope (MCP server, MCP client, document RAG, combined workflow), your assigned use case, minimum functional requirements, architecture expectations, the mandatory end-to-end acceptance scenario, deliverables, and the suggested time box. **Start here.** |
| `Submission_and_Evaluation_Guidelines.md` | How to submit your work: GitHub repository structure, README requirements, MCP and RAG documentation, packaging, CI, and security expectations. Also contains the evaluation scoring framework, red flags, and the submission message template. Includes the requirement to upload a demo video of up to 10 minutes. |
| `postman/` | Reference Postman collections for the Alarm Management API. Use these as the specification to build your own simulator backend — the MCP server must connect to that simulator. Includes `Alarm-API-Simulator.postman_collection.json` (full E2E baseline), `scenarios/` (scenario-focused tests), and `chaining/` (ten multi-step chaining flows). |

## How to get started

1. Read `Assignment_Use_Case.md` end to end.
2. Read `Submission_and_Evaluation_Guidelines.md` to understand submission and scoring.
3. Implement the Alarm Management API simulator using the Postman collections in `postman/` as the API contract specification.
4. Build one complete vertical slice: MCP server + MCP client + document RAG + GUI, integrated in a single workflow.
5. Record a demo video of up to 10 minutes and link it in your repository README.

## Key reminders

- The copilot must call the Alarm Management API **through your MCP server**, not directly.
- MCP and RAG must participate in the **same** business workflow, not as separate demonstrations.
- Answers must include **source citations** and an **MCP execution trace**.
- This use case involves a **write operation** (ticket creation). It must require **explicit confirmation** before writing.
- Do not commit secrets. Provide a `.env.example`.
- Include automated tests and repeatable packaging (`docker compose up --build`).
- Suggested time box: 10 to 14 hours.

Good luck.
