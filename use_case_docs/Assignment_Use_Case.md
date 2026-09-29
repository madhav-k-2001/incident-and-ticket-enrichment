# Senior Software Engineer – Copilot Integration Assignment

## Incident and Ticket Enrichment Copilot

> **This is your assigned use case.** Implement it completely.

---

## 1. Objective

This assignment evaluates the candidate’s ability to design and implement a production-oriented copilot application that integrates with enterprise source systems.

The candidate is expected to demonstrate:

- Strong software architecture and design decisions
- Deep API and source-system integration knowledge
- MCP server development and MCP client integration
- Document ingestion and Retrieval-Augmented Generation
- Reliable orchestration across multiple tools and data sources
- A usable graphical interface
- Test-driven development
- Secure configuration and operational readiness
- Clear packaging, documentation, and deployment instructions

The candidate must build an **Alarm Management API simulator** as the backend source system that the MCP server connects to. The `postman/` folder in this package contains Postman collections that serve as the reference API specification — they define every endpoint, request schema, response structure, authentication header, trace header, and chaining flow the simulator must honour. The candidate should use those collections to implement and validate their simulator before wiring it to the MCP server.

The required API surface includes asset search, asset metadata, alarm retrieval, alarm summaries, trends, correlation, flood analysis, rationalization candidates, priority scoring, operator recommendations, KPI calculation, authentication, trace metadata propagation, pagination, and multi-step API chaining.

---

## 2. Mandatory Technical Scope

The following components are mandatory for every submission, irrespective of the selected business use case.

### 2.1 Mandatory MCP Server Development

The candidate must develop at least one working MCP server.

The MCP server must:

- Expose selected Alarm Management API capabilities as MCP tools
- Provide meaningful tool names and descriptions
- Define typed input and output schemas
- Validate tool inputs
- Handle authentication and configuration
- Map external API errors into understandable MCP errors
- Support timeout and retry handling
- Propagate correlation or trace metadata
- Avoid exposing secrets in logs or responses
- Be independently runnable and testable

At minimum, the MCP server should expose tools for:

- Asset search
- Alarm retrieval
- Alarm summary or trend analysis
- One advanced operation such as correlation, rationalization, priority scoring, recommendations, or KPI calculation

The candidate may expose additional systems through the same MCP server or through a second MCP server.

### 2.2 Mandatory MCP Client Integration

The copilot application must connect to and invoke the developed MCP server.

The implementation must demonstrate:

- Tool discovery
- Schema-aware tool invocation
- Multi-step tool chaining
- Passing outputs from one MCP tool into another
- Handling invalid tool inputs
- Handling unavailable tools
- Handling partial failures
- Displaying MCP execution details in the GUI

Direct API calls may be used internally by the MCP server, but the copilot orchestration layer must use MCP for the Alarm Management API integration.

### 2.3 Mandatory Document RAG Capability

Every solution must include a document-based RAG workflow.

The candidate must provide a small document corpus relevant to the selected use case, such as:

- Operating procedures
- Troubleshooting manuals
- Alarm philosophy documents
- Maintenance guides
- Safety instructions
- Service knowledge articles
- Ticket resolution notes
- Engineering standards

The RAG implementation must include:

- Document ingestion
- Text extraction
- Chunking
- Metadata capture
- Embedding generation or an equivalent retrieval approach
- Vector or hybrid retrieval
- Retrieval filtering
- Source citations
- Grounded answer generation
- Handling of no-result or low-confidence retrieval
- Protection against prompt instructions embedded inside retrieved documents

The GUI must show the document sources used to produce the answer.

### 2.4 Mandatory Combined Workflow

At least one end-to-end scenario must combine:

1. Natural-language request
2. MCP tool discovery and execution
3. Alarm Management API data
4. Document retrieval through RAG
5. Multi-step orchestration
6. A grounded answer with evidence
7. GUI presentation
8. Tool and source traceability

A solution that implements MCP and RAG as unrelated demonstrations will not be considered complete. They must participate in the same business workflow.

---

## 3. Candidate Assignment

The candidate should implement **one primary use case completely**.

The completed solution should:

1. Accept natural-language requests.
2. Identify the correct intent.
3. Discover and invoke MCP tools.
4. Perform multi-step tool chaining.
5. Retrieve relevant document evidence through RAG.
6. Combine structured and unstructured information.
7. Present the result through a usable GUI.
8. Show source evidence, tool traceability, and failure handling.
9. Include automated tests and repeatable packaging.

The implementation should not be a hard-coded sequence for only the sample questions.

---

## 4. Your Assigned Use Case — Incident and Ticket Enrichment Copilot

### Business Scenario

When a high-priority alarm occurs, service teams need to create or update a support ticket with accurate alarm context, similar historical cases, and documented troubleshooting guidance.

Build a copilot that combines MCP-based alarm integration, ticketing, and document RAG.

### Example Questions

- Prepare an incident for the highest-priority active alarm in EastRefinery.
- Find similar historical tickets for this compressor alarm.
- Summarize the issue, likely cause, affected asset, and recommended action.
- Add the applicable troubleshooting procedure to the ticket draft.
- Show open tickets linked to correlated assets.

### Required Integrations

#### MCP server

The candidate must expose the Alarm Management API as MCP tools.

The candidate must also expose ticketing capabilities through:

- The same MCP server, or
- A second MCP server

Supported ticketing options:

- Jira
- Azure DevOps
- ServiceNow
- GitHub Issues
- Candidate-built mock ticketing API

#### Document RAG

Ingest:

- Troubleshooting guides
- Support knowledge articles
- Historical resolution notes
- Escalation procedures

### Expected Workflow

1. Retrieve and prioritize alarms through MCP.
2. Enrich the selected alarm with asset context.
3. Retrieve recommended actions.
4. Search similar tickets.
5. Retrieve relevant support documents.
6. Prepare a structured incident draft.
7. Require explicit approval before a ticket write operation.
8. Return the created ticket identifier or draft preview.

### GUI Expectations

- Chat interface
- Alarm-to-ticket preview
- Editable ticket fields
- Similar-ticket panel
- Document citations
- Confirmation step for write operations
- Audit trail

---

## 5. Minimum Functional Requirements

### Copilot

- Natural-language input
- Intent detection or planning
- MCP tool discovery
- Multi-step MCP orchestration
- Context retention
- Structured final responses
- Source citations
- Graceful handling of incomplete information

### MCP

- At least one candidate-developed MCP server
- MCP client integration
- Typed schemas
- Tool validation
- Authentication handling
- Timeout and retry behavior
- Tool error mapping
- Trace metadata
- Automated MCP tests

### RAG

- Document ingestion
- Chunking and metadata
- Retrieval index
- Grounded generation
- Citations
- Low-confidence handling
- Prompt-injection considerations
- Retrieval tests

### GUI

The GUI may use React, Angular, Vue, Streamlit, Gradio, or another appropriate framework.

It must provide:

- Chat interaction
- Structured results
- MCP execution visibility
- RAG citations
- Loading, error, and empty states
- Configuration through environment variables
- Responsive presentation

### Tests

At minimum:

- Unit tests
- MCP server tool tests
- MCP client integration tests
- RAG ingestion and retrieval tests
- Orchestration tests
- API client tests
- Error-path tests
- One end-to-end test combining MCP and RAG

---

## 6. Architecture Expectations

The architecture must clearly separate:

- User interface
- Copilot orchestration layer
- MCP client or tool registry
- Candidate-developed MCP server
- API and source-system connectors
- RAG ingestion pipeline
- Retrieval service
- Domain models
- Authentication and configuration
- Observability
- Persistence, where used

The submission must include an architecture diagram that explicitly shows both the MCP and RAG paths.

---

## 7. Mandatory End-to-End Acceptance Scenario

The candidate must demonstrate at least one scenario similar to:

> Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify likely contributing factors, retrieve the relevant operating procedure, and provide recommended actions with source evidence.

The scenario should show:

1. Asset resolution through an MCP tool
2. Multi-step Alarm Management API chaining through MCP
3. Document retrieval through RAG
4. Combined reasoning
5. Citations
6. GUI output
7. MCP execution trace
8. Automated end-to-end test evidence

---

## 8. Deliverables

The GitHub repository must contain:

1. Copilot source code
2. Candidate-developed MCP server source code
3. MCP client integration
4. RAG ingestion pipeline
5. Sample document corpus
6. Retrieval index creation instructions
7. GUI source code
8. README
9. Architecture document
10. Architecture diagram showing MCP and RAG
11. API integration documentation
12. MCP tool catalog
13. Test suite
14. Sample environment file
15. Dockerfile
16. Docker Compose or equivalent
17. Demo screenshots or recording
18. Coverage report
19. Known limitations
20. Future improvements
21. A demo video of up to 10 minutes showcasing the working solution

---

## 9. Suggested Time Box

Recommended time box: **10 to 14 hours**.

The candidate should prioritize:

- One complete vertical slice
- Working MCP server and MCP client integration
- Working document RAG
- Clean architecture
- Automated tests
- Repeatable packaging

A smaller, fully integrated MCP-plus-RAG solution is preferred over a broad but incomplete implementation.
