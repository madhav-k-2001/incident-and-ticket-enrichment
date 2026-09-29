# GitHub Submission and Evaluation Guidelines

## 1. Mandatory Submission Scope

Every submission must include:

- A working copilot application
- A candidate-developed MCP server
- MCP client integration in the copilot
- A document ingestion and RAG workflow
- A GUI
- Automated tests
- Repeatable local packaging
- A demo video of up to 10 minutes showcasing the working solution

MCP development and document RAG are mandatory and must participate in the same end-to-end workflow.

A submission will be considered incomplete when:

- The MCP server is only a stub
- The copilot bypasses MCP and calls the Alarm Management API directly
- RAG is implemented only as a disconnected sample
- Document citations are missing
- MCP and RAG are not demonstrated together

## 2. Repository Submission

Create a GitHub repository and share the link for evaluation.

The repository may be public or private with evaluator access granted.

Recommended name:

```text
senior-copilot-mcp-rag-assignment
```

## 3. Required Repository Structure

```text
.
├── README.md
├── docs/
│   ├── architecture.md
│   ├── architecture-diagram.png
│   ├── mcp-tool-catalog.md
│   ├── rag-design.md
│   ├── api-integration.md
│   ├── design-decisions.md
│   └── known-limitations.md
├── apps/
│   ├── backend/
│   └── frontend/
├── mcp-servers/
│   ├── alarm-management/
│   └── optional-secondary-server/
├── rag/
│   ├── ingestion/
│   ├── retrieval/
│   ├── documents/
│   └── tests/
├── connectors/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── test-data/
├── scripts/
├── .github/workflows/ci.yml
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── Makefile
└── LICENSE
```

Equivalent structures are acceptable when clearly documented.

## 4. README Requirements

The root README must include:

- Selected use case
- Main capabilities
- Technology stack
- MCP server description
- MCP tool list
- RAG corpus and ingestion approach
- Quick-start instructions
- Configuration
- Build and run commands
- Test commands
- Sample interactions
- Architecture summary
- Assumptions
- Known limitations

## 5. Mandatory MCP Documentation

Include `docs/mcp-tool-catalog.md`.

For every MCP tool, document:

- Tool name
- Purpose
- Input schema
- Output schema
- Authentication behavior
- Underlying source-system operation
- Error behavior
- Timeout behavior
- Example invocation
- Example response

The README must explain how to start the MCP server independently.

## 6. Mandatory MCP Engineering Requirements

The MCP implementation should demonstrate:

- Tool discovery
- Typed contracts
- Input validation
- Output validation
- Configuration management
- Authentication propagation
- Correlation and trace metadata
- Pagination handling
- Timeout handling
- Retry behavior
- API error mapping
- Structured logging
- Safe secret handling

The copilot orchestration layer must invoke the Alarm Management API through the MCP server.

## 7. Mandatory RAG Documentation

Include `docs/rag-design.md`.

Document:

- Source document types
- Ingestion flow
- Text extraction
- Chunking strategy
- Chunk metadata
- Embedding model or retrieval method
- Vector database or index
- Hybrid search, when used
- Ranking or reranking
- Retrieval filters
- Citation construction
- Low-confidence handling
- Prompt-injection protections
- Index refresh process

## 8. Mandatory RAG Repository Content

The repository must include:

- A representative sample document corpus
- An ingestion command or script
- Index creation instructions
- Retrieval tests
- Example retrieved chunks
- Document metadata
- Citation examples

Large or restricted documents should not be committed. In such cases, provide synthetic or public sample documents with the same expected structure.

## 9. Architecture Documentation

The architecture diagram must show:

- GUI
- Copilot orchestration
- MCP client
- MCP server
- Alarm Management API
- Optional secondary source
- RAG ingestion pipeline
- Retrieval index
- Document store
- Observability
- Authentication boundaries

The architecture document should explain the complete request flow from user prompt to final grounded answer.

## 10. Configuration and Secrets

Do not commit:

- API keys
- Access tokens
- Database passwords
- Cloud credentials
- Certificates

Provide `.env.example`.

Example:

```text
ALARM_API_BASE_URL=http://alarm-api:8000
ALARM_API_TOKEN=replace-me
MCP_SERVER_URL=http://alarm-mcp:9000
LLM_PROVIDER=replace-me
LLM_API_KEY=replace-me
VECTOR_STORE_URL=replace-me
DOCUMENT_PATH=./rag/documents
TICKETING_API_URL=replace-me
```

## 11. Branching and Commit Expectations

Use meaningful commits.

Examples:

```text
feat: add alarm management mcp server
feat: add document ingestion pipeline
feat: add rag retrieval with citations
test: add mcp tool contract tests
test: add end-to-end mcp and rag scenario
docs: document architecture and tool catalog
```

At least one pull request is expected.

## 12. Pull Request Expectations

The pull request should contain:

- Summary
- Scope
- Architecture changes
- MCP tools added
- RAG workflow added
- Screenshots
- Test evidence
- Design decisions
- Known limitations
- Review checklist

## 13. Test-Driven Development Expectations

### Unit tests

- Payload construction
- Input validation
- Response parsing
- Tool selection
- Citation formatting
- Retrieval filtering

### MCP server tests

- Tool registration
- Tool discovery
- Schema validation
- Authentication headers
- Pagination
- Timeouts
- Retries
- API error mapping
- Trace propagation

### MCP client tests

- Server connectivity
- Tool discovery
- Tool invocation
- Invalid arguments
- Missing tools
- Partial failure

### RAG tests

- Document ingestion
- Chunking
- Metadata capture
- Retrieval relevance
- Citation correctness
- No-result behavior
- Prompt-injection handling

### Orchestration tests

- Multi-step MCP chains
- MCP output passed into subsequent tools
- RAG retrieval within the same workflow
- Combined answer generation
- Partial source failure
- Conflicting evidence

### End-to-end test

At least one automated scenario must combine:

- GUI or backend request
- MCP server invocation
- Alarm Management API
- RAG retrieval
- Grounded response with citations

## 14. Packaging Requirements

Preferred startup:

```text
docker compose up --build
```

Docker Compose should start, as applicable:

- Alarm Management API
- MCP server
- Copilot backend
- GUI
- Vector database or retrieval service
- Optional database or ticketing mock

Provide health checks and service dependency ordering.

## 15. Continuous Integration

GitHub Actions should run:

- Formatting
- Linting
- Static analysis
- Unit tests
- MCP tests
- RAG tests
- Integration tests
- Build validation
- Security or dependency checks where practical

## 16. Observability

Preferred fields:

- Request ID
- Conversation ID
- Trace ID
- MCP server
- MCP tool
- Tool duration
- Tool outcome
- API status code
- Retry count
- Retrieval query
- Retrieved document identifiers
- Retrieval score
- LLM latency

Logs must not expose secrets or complete sensitive documents.

## 17. Security Expectations

Address:

- Secret management
- MCP tool authorization
- Input validation
- Output encoding
- Prompt injection
- Retrieved-document trust boundaries
- SQL injection prevention
- Write-operation approval
- Tool misuse
- Dependency risk

Ticket or issue creation must require explicit confirmation.

## 18. Demo Evidence

Include:

- A demo video of up to 10 minutes that showcases the work done, uploaded and linked in the README
- Screenshots or short recording
- MCP tool-discovery view
- MCP execution trace
- RAG citations
- One successful scenario
- One failure or degraded scenario

The 10-minute demo video should walk through the end-to-end workflow, including MCP tool discovery and execution, RAG citations, and at least one successful and one failure or degraded scenario. Upload it to an accessible location (for example, the repository release assets, a shared drive, or a video platform) and link it from the README.

## 19. Repository Sharing Checklist

Confirm:

- Repository is accessible
- MCP server runs independently
- Copilot connects through MCP
- Sample documents are present
- RAG ingestion succeeds
- Citations are visible
- Combined MCP-plus-RAG scenario works
- Setup works from a clean environment
- No secrets are committed
- Tests pass
- Architecture diagram is included
- GitHub Actions status is visible
- Evaluator access is granted
- A demo video of up to 10 minutes is uploaded and linked

## 20. Submission Message Template

```text
Subject: Senior Software Engineer Copilot Assignment Submission

Repository:
<GitHub repository URL>

Selected use case:
<Use case name>

MCP server:
<Brief description and start command>

Document RAG:
<Corpus, ingestion command, and retrieval approach>

Run instructions:
<Primary command>

Test instructions:
<Primary command>

Demo:
<Location of screenshots or recording>

Demo video (up to 10 minutes):
<Link to uploaded video>

Known limitations:
<Brief list>

Estimated implementation time:
<Hours>
```

# Evaluation Framework

## 21. Scoring Summary

| Area | Weight |
|---|---:|
| Architecture and design | 20% |
| MCP server development and integration | 20% |
| Document RAG implementation | 15% |
| Approach and completeness | 15% |
| Test-driven development and code quality | 20% |
| Packaging, documentation, and operability | 10% |

## 22. Architecture and Design – 20%

Evaluate:

- Separation of concerns
- MCP boundaries
- RAG boundaries
- Reusable connectors
- Typed contracts
- Replaceable LLM provider
- Security boundaries
- Observability
- Maintainability

## 23. MCP Server Development and Integration – 20%

Evaluate:

- Actual server implementation
- Quality of tool contracts
- Tool discovery
- Schema validation
- Authentication
- Error handling
- Pagination
- Retry and timeout behavior
- Trace propagation
- Multi-step chaining
- GUI visibility

## 24. Document RAG Implementation – 15%

Evaluate:

- Ingestion design
- Chunking
- Metadata
- Retrieval quality
- Citations
- Grounding
- Low-confidence handling
- Prompt-injection considerations
- Reproducibility

## 25. Approach and Completeness – 15%

Evaluate:

- Problem decomposition
- Correct tool selection
- MCP and RAG used together
- Functional GUI
- Evidence-backed output
- Error handling
- Representative business flow

## 26. Test-Driven Development and Code Quality – 20%

Evaluate:

- Unit tests
- MCP contract tests
- RAG tests
- Orchestration tests
- Error-path tests
- End-to-end tests
- CI
- Code readability
- Static analysis

## 27. Packaging, Documentation, and Operability – 10%

Evaluate:

- Clean setup
- Repeatable build
- Docker packaging
- Environment configuration
- Health checks
- README quality
- MCP tool catalog
- RAG design document
- Demo evidence
- Repository hygiene

## 28. Red Flags

- MCP server is mocked without real tool execution
- Copilot bypasses MCP
- RAG is disconnected from the main use case
- No source citations
- Hard-coded answers
- Secrets committed
- No automated tests
- Unsafe SQL
- Write operations without approval
- No error handling
- Repository cannot run using documented steps
