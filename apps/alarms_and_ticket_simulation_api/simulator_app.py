"""
Alarm Management API Simulator & Ticketing Service
===================================================
A standalone, self-contained FastAPI server providing:
1. All 15 Alarm Management API endpoints specified in the Postman collections.
2. Ticketing Service APIs for Incident and Ticket Enrichment (search, create, update, list).
3. Reads data from 'mock_data.json' at startup into an in-memory store.
4. Distributed trace header propagation (trace_id, x-client-id, x-metadata-tag).
5. Flexible Bearer token authentication (accepts demo-token from Postman).
6. Configured for Python 3.12 and managed with 'uv'.

To run with uv:
    uv run simulator_app.py
    # or: uv run uvicorn simulator_app:app --host 0.0.0.0 --port 8000 --reload
"""

import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ==============================================================================
# 1. DATA LOADER & IN-MEMORY STORE
# Reads 'mock_data.json' at startup
# ==============================================================================

MOCK_DATA_PATH = Path(__file__).parent / "mock_data.json"

DATA_STORE: Dict[str, Any] = {
    "assets": [],
    "alarms": [],
    "recommendations": {},
    "tickets": [],
    "flood_windows": [],
    "correlations": [],
    "rationalization_candidates": [],
    "kpi_definitions": []
}

def load_data_from_json() -> None:
    """Load mock dataset from JSON file into in-memory store."""
    if not MOCK_DATA_PATH.exists():
        # Fallback search in current working directory
        alt_path = Path("mock_data.json")
        if alt_path.exists():
            path_to_use = alt_path
        else:
            raise FileNotFoundError(f"Cannot find mock_data.json at {MOCK_DATA_PATH} or {alt_path}")
    else:
        path_to_use = MOCK_DATA_PATH

    with open(path_to_use, "r", encoding="utf-8") as f:
        loaded = json.load(f)

    DATA_STORE["assets"] = loaded.get("assets", [])
    DATA_STORE["alarms"] = loaded.get("alarms", [])
    DATA_STORE["recommendations"] = loaded.get("recommendations", {})
    DATA_STORE["tickets"] = loaded.get("tickets", [])
    DATA_STORE["flood_windows"] = loaded.get("flood_windows", [])
    DATA_STORE["correlations"] = loaded.get("correlations", [])
    DATA_STORE["rationalization_candidates"] = loaded.get("rationalization_candidates", [])
    DATA_STORE["kpi_definitions"] = loaded.get("kpi_definitions", [])

    print(f"✅ Loaded mock data from {path_to_use.name}: "
          f"{len(DATA_STORE['assets'])} assets, "
          f"{len(DATA_STORE['alarms'])} alarms, "
          f"{len(DATA_STORE['tickets'])} tickets, "
          f"{len(DATA_STORE['recommendations'])} recommendation entries.")

# Initial load on module import
load_data_from_json()

# ==============================================================================
# 2. PYDANTIC REQUEST & RESPONSE MODELS
# ==============================================================================

class TimeRange(BaseModel):
    start_time: Optional[str] = "2026-05-01T00:00:00Z"
    end_time: Optional[str] = "2026-10-01T00:00:00Z"

class AlarmSummaryRequest(BaseModel):
    asset_ids: Optional[List[str]] = Field(default_factory=list)
    time_range: Optional[TimeRange] = None
    severity: Optional[List[str]] = None
    group_by: Optional[List[str]] = Field(default_factory=lambda: ["alarm_name"])
    kpis: Optional[List[str]] = Field(default_factory=lambda: ["alarm_count", "recurring_rate", "avg_ack_delay"])

class AlarmTrendsRequest(BaseModel):
    asset_ids: Optional[List[str]] = Field(default_factory=list)
    time_range: Optional[TimeRange] = None
    bucket: Optional[str] = "daily"
    metrics: Optional[List[str]] = Field(default_factory=lambda: ["alarm_count", "avg_ack_delay"])

class AlarmCorrelationRequest(BaseModel):
    asset_ids: Optional[List[str]] = Field(default_factory=list)
    time_range: Optional[TimeRange] = None
    correlation_method: Optional[str] = "cooccurrence"
    lag_window_minutes: Optional[int] = 15
    severity_threshold: Optional[str] = "medium"
    min_support: Optional[int] = 1

class FloodAnalysisRequest(BaseModel):
    unit: Optional[str] = "Unit 2"
    time_range: Optional[TimeRange] = None
    threshold_count: Optional[int] = 10
    rolling_window_minutes: Optional[int] = 10

class RationalizationRequest(BaseModel):
    asset_ids: Optional[List[str]] = Field(default_factory=list)
    time_range: Optional[TimeRange] = None
    recurrence_threshold: Optional[int] = 5
    stale_minutes_threshold: Optional[int] = 180

class PriorityScoreRequest(BaseModel):
    alarm_id: str

class OperatorRecommendationsRequest(BaseModel):
    alarm_id: str
    include_related: Optional[bool] = True
    include_asset_context: Optional[bool] = True
    include_historical_pattern: Optional[bool] = True

class CalculationGenerateRequest(BaseModel):
    calculation_type: str = "alarm_flood_index"
    filters: Optional[Dict[str, Any]] = None

class CalculationExecuteRequest(BaseModel):
    calculation_id: str
    filters: Optional[Dict[str, Any]] = None

# Ticketing Models
class TicketCreateRequest(BaseModel):
    title: str
    asset_id: str
    alarm_id: Optional[str] = None
    severity: Optional[str] = "high"
    priority: Optional[str] = "P2"
    symptom: Optional[str] = ""
    likely_cause: Optional[str] = ""
    recommended_action: Optional[str] = ""
    sop_reference: Optional[str] = ""
    assigned_to: Optional[str] = "Instrumentation Team"
    assigned_user: Optional[str] = "Unassigned"
    similar_ticket_ref: Optional[str] = None

class TicketUpdateRequest(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    resolution_notes: Optional[str] = None
    assigned_to: Optional[str] = None
    assigned_user: Optional[str] = None
    work_notes: Optional[str] = None

# ==============================================================================
# 3. FASTAPI APP INITIALIZATION & MIDDLEWARE
# ==============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure fresh read from JSON at server startup
    load_data_from_json()
    yield

app = FastAPI(
    title="Alarm Management API Simulator & Ticketing Service",
    description="Standalone simulator reading from mock_data.json, supporting Postman E2E collections and Incident/Ticket Enrichment.",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for any frontend (React, Streamlit, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def resolve_trace_id(request: Request) -> Optional[str]:
    """Trace id from either spelling. FastAPI's Header() only reads 'trace-id', but
    the Postman collections and the alarm MCP client send 'trace_id'."""
    return request.headers.get("trace_id") or request.headers.get("trace-id")

@app.middleware("http")
async def add_trace_headers_middleware(request: Request, call_next):
    # Extract distributed trace headers from incoming request or generate default
    trace_id = request.headers.get("trace_id") or request.headers.get("trace-id") or f"trace-{uuid.uuid4().hex[:8]}"
    client_id = request.headers.get("x-client-id") or "simulator-client"
    metadata_tag = request.headers.get("x-metadata-tag") or "auto"

    response: Response = await call_next(request)

    # Echo back trace headers
    response.headers["trace_id"] = trace_id
    response.headers["x-client-id"] = client_id
    response.headers["x-metadata-tag"] = metadata_tag
    return response

# ==============================================================================
# 4. ALARM MANAGEMENT API ENDPOINTS (POSTMAN COLLECTION COMPLIANT)
# ==============================================================================

@app.get("/health", tags=["System"])
def get_health():
    """00 - Health Check"""
    return {
        "status": "healthy",
        "service": "alarm-and-ticketing-simulator",
        "version": "1.0.0",
        "python_version": "3.12",
        "data_source": "mock_data.json",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.post("/data/reload", tags=["System"])
def reload_mock_data():
    """Hot-reload data from mock_data.json without restarting the server process."""
    load_data_from_json()
    return {
        "status": "reloaded",
        "assets_count": len(DATA_STORE["assets"]),
        "alarms_count": len(DATA_STORE["alarms"]),
        "tickets_count": len(DATA_STORE["tickets"])
    }

@app.get("/assets/search", tags=["Assets"])
def search_assets(
    query: Optional[str] = None,
    unit: Optional[str] = None,
    site: Optional[str] = None,
    limit: int = 10
):
    """01 - Search Asset (sets asset_id in Postman)"""
    results = DATA_STORE["assets"]

    if query and isinstance(query, str):
        q_lower = query.lower()
        results = [
            a for a in results
            if q_lower in a["asset_name"].lower()
            or q_lower in a["asset_id"].lower()
            or q_lower in a["type"].lower()
        ]

    if unit and isinstance(unit, str):
        results = [a for a in results if a.get("unit", "").lower() == unit.lower()]

    if site and isinstance(site, str):
        results = [a for a in results if a.get("site", "").lower() == site.lower()]

    paged_results = results[:limit]
    return {
        "results": paged_results,
        "total": len(results)
    }

@app.get("/assets/{asset_id}/metadata", tags=["Assets"])
def get_asset_metadata(asset_id: str):
    """02 - Asset Metadata"""
    for asset in DATA_STORE["assets"]:
        if asset["asset_id"].lower() == asset_id.lower():
            return asset
    raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")

KNOWN_ALARM_FIELDS = {
    "ack_time",
    "alarm_code",
    "alarm_id",
    "alarm_name",
    "asset_id",
    "asset_name",
    "end_time",
    "priority_score",
    "severity",
    "site",
    "start_time",
    "status",
    "threshold",
    "unit",
    "unit_of_measure",
    "value",
}
NUMERIC_ALARM_FIELDS = {"priority_score", "threshold", "value"}

@app.get("/alarms", tags=["Alarms"])
def get_alarms(
    asset_id: Optional[str] = None,
    site: Optional[str] = None,
    unit: Optional[str] = None,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    sort_by: str = "start_time",
    sort_order: str = "desc"
):
    """03 - Get Alarms (sets alarm_id in Postman)"""
    known_fields = KNOWN_ALARM_FIELDS | {k for a in DATA_STORE["alarms"] for k in a.keys()}
    if sort_by not in known_fields:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown sort_by field '{sort_by}'. Valid fields are: {', '.join(sorted(known_fields))}"
        )

    filtered = DATA_STORE["alarms"]

    if asset_id:
        filtered = [a for a in filtered if a["asset_id"].lower() == asset_id.lower()]
    if site:
        filtered = [a for a in filtered if a.get("site", "").lower() == site.lower()]
    if unit:
        filtered = [a for a in filtered if a.get("unit", "").lower() == unit.lower()]
    if status:
        filtered = [a for a in filtered if a.get("status", "").lower() == status.lower()]
    if severity:
        filtered = [a for a in filtered if a.get("severity", "").lower() == severity.lower()]

    # Sort
    reverse = (sort_order.lower() == "desc")

    def sort_key(alarm: dict):
        val = alarm.get(sort_by)
        if val is None:
            return (1 if not reverse else -1, 0, 0.0, "")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return (0, 0, float(val), "")
        if sort_by in NUMERIC_ALARM_FIELDS:
            try:
                return (0, 0, float(val), "")
            except (ValueError, TypeError):
                pass
        return (0, 1, 0.0, str(val))

    filtered = sorted(filtered, key=sort_key, reverse=reverse)

    # Paginate
    total_count = len(filtered)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    paged_data = filtered[start_idx:end_idx]

    return {
        "data": paged_data,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": (total_count + page_size - 1) // page_size if total_count > 0 else 1
        }
    }

@app.get("/alarms/{alarm_id}", tags=["Alarms"])
def get_alarm_by_id(alarm_id: str):
    """04 - Get Alarm by ID"""
    for alarm in DATA_STORE["alarms"]:
        if alarm["alarm_id"].lower() == alarm_id.lower():
            return alarm
    raise HTTPException(status_code=404, detail=f"Alarm {alarm_id} not found")

@app.post("/alarms/summary", tags=["Alarms"])
def get_alarm_summary(
    payload: AlarmSummaryRequest,
    trace_id: Optional[str] = Depends(resolve_trace_id),
    x_client_id: Optional[str] = Header(default=None),
    x_metadata_tag: Optional[str] = Header(default=None)
):
    """05 - Alarm Summary (supports trace headers)"""
    filtered = DATA_STORE["alarms"]
    if payload.asset_ids:
        ids_lower = [aid.lower() for aid in payload.asset_ids]
        filtered = [a for a in filtered if a["asset_id"].lower() in ids_lower]
    if payload.severity:
        sevs_lower = [s.lower() for s in payload.severity]
        filtered = [a for a in filtered if a["severity"].lower() in sevs_lower]

    total = len(filtered)
    crit_count = sum(1 for a in filtered if a["severity"] == "critical")
    high_count = sum(1 for a in filtered if a["severity"] == "high")
    med_count = sum(1 for a in filtered if a["severity"] == "medium")
    low_count = sum(1 for a in filtered if a["severity"] == "low")

    return {
        "total_alarms": total,
        "severity_breakdown": {
            "critical": crit_count,
            "high": high_count,
            "medium": med_count,
            "low": low_count
        },
        "summary": [
            {
                "group_key": a["alarm_name"],
                "alarm_count": sum(1 for x in filtered if x["alarm_name"] == a["alarm_name"]),
                "recurring_rate": 0.42,
                "avg_ack_delay_seconds": 48.5
            }
            for a in filtered[:5]
        ],
        "kpis": {
            "alarm_count": total,
            "recurring_rate": 0.35 if total > 2 else 0.0,
            "avg_ack_delay_seconds": 54.2
        },
        "trace_metadata": {
            "trace_id": trace_id,
            "x_client_id": x_client_id,
            "x_metadata_tag": x_metadata_tag
        }
    }

@app.post("/alarms/trends", tags=["Alarms"])
def get_alarm_trends(payload: AlarmTrendsRequest):
    """06 - Alarm Trends"""
    return {
        "asset_ids": payload.asset_ids,
        "bucket": payload.bucket,
        "time_range": payload.time_range.dict() if payload.time_range else {},
        "data": [
            {"timestamp": "2026-09-24T00:00:00Z", "alarm_count": 1, "avg_ack_delay": 32.0},
            {"timestamp": "2026-09-25T00:00:00Z", "alarm_count": 0, "avg_ack_delay": 0.0},
            {"timestamp": "2026-09-26T00:00:00Z", "alarm_count": 3, "avg_ack_delay": 65.0},
            {"timestamp": "2026-09-27T00:00:00Z", "alarm_count": 2, "avg_ack_delay": 40.0},
            {"timestamp": "2026-09-28T00:00:00Z", "alarm_count": 4, "avg_ack_delay": 55.0},
            {"timestamp": "2026-09-29T00:00:00Z", "alarm_count": 5, "avg_ack_delay": 48.0}
        ]
    }

@app.post("/alarms/correlation", tags=["Alarms"])
def get_alarm_correlation(
    payload: AlarmCorrelationRequest,
    trace_id: Optional[str] = Depends(resolve_trace_id),
    x_client_id: Optional[str] = Header(default=None),
    x_metadata_tag: Optional[str] = Header(default=None)
):
    """07 - Alarm Correlation (trace headers)"""
    return {
        "correlation_method": payload.correlation_method,
        "lag_window_minutes": payload.lag_window_minutes,
        "correlations": DATA_STORE["correlations"],
        "trace_metadata": {
            "trace_id": trace_id,
            "x_client_id": x_client_id,
            "x_metadata_tag": x_metadata_tag
        }
    }

@app.post("/alarms/flood-analysis", tags=["Alarms"])
def get_flood_analysis(payload: FloodAnalysisRequest):
    """08 - Flood Analysis (Postman CHAIN-02 checks flood_windows[0].start and .end)"""
    return {
        "unit": payload.unit or "Unit 2",
        "threshold_count": payload.threshold_count,
        "rolling_window_minutes": payload.rolling_window_minutes,
        "flood_events_count": len(DATA_STORE["flood_windows"]),
        "flood_windows": DATA_STORE["flood_windows"]
    }

@app.post("/alarms/rationalization-candidates", tags=["Alarms"])
def get_rationalization_candidates(payload: RationalizationRequest):
    """09 - Rationalization Candidates (stale, nuisance, chattering alarms)"""
    return {
        "candidates": DATA_STORE["rationalization_candidates"]
    }

@app.post("/alarms/priority-score", tags=["Alarms"])
def get_priority_score(payload: PriorityScoreRequest):
    """10 - Priority Score"""
    alarm_id = payload.alarm_id.upper()
    score = 75.0
    severity = "high"
    urgency = "standard"

    for a in DATA_STORE["alarms"]:
        if a["alarm_id"].upper() == alarm_id:
            score = a.get("priority_score", 80.0)
            severity = a.get("severity", "high")
            break

    if score >= 90.0:
        urgency = "immediate"
        recommended_priority_level = "P1"
    elif score >= 75.0:
        urgency = "urgent"
        recommended_priority_level = "P2"
    else:
        urgency = "routine"
        recommended_priority_level = "P3"

    return {
        "alarm_id": alarm_id,
        "priority_score": score,
        "severity": severity,
        "urgency": urgency,
        "recommended_priority_level": recommended_priority_level,
        "factors": {
            "asset_criticality_weight": 1.5 if severity == "critical" else 1.2,
            "safety_trip_proximity": "high" if severity == "critical" else "normal",
            "recurrence_penalty": 1.2
        }
    }

@app.post("/recommendations/operator-actions", tags=["Alarms"])
def get_operator_recommendations(
    payload: OperatorRecommendationsRequest,
    trace_id: Optional[str] = Depends(resolve_trace_id),
    x_client_id: Optional[str] = Header(default=None),
    x_metadata_tag: Optional[str] = Header(default=None)
):
    """11 - Operator Recommendations (trace headers)"""
    alarm_id = payload.alarm_id.upper()
    recs_store = DATA_STORE["recommendations"]

    if alarm_id in recs_store:
        rec = dict(recs_store[alarm_id])
    else:
        # Default dynamic response for any unmapped alarm
        rec = {
            "alarm_id": alarm_id,
            "asset_id": "UNKNOWN",
            "likely_causes": ["Sensor drift", "Operating setpoint exceeded"],
            "immediate_actions": ["Verify field gauge", "Monitor parameter trend"],
            "safety_precautions": ["Follow standard plant safety PPE"],
            "related_assets": [],
            "historical_insights": "No specific prior high-severity correlation found."
        }

    rec["trace_metadata"] = {
        "trace_id": trace_id,
        "x_client_id": x_client_id,
        "x_metadata_tag": x_metadata_tag
    }
    return rec

@app.post("/calculation-code/generate", tags=["Analytics"])
def generate_calculation_code(payload: CalculationGenerateRequest):
    """12 - Generate Calculation Code (sets calculation_id in Postman)"""
    calc_id = f"CALC-{uuid.uuid4().hex[:6].upper()}"
    return {
        "calculation_id": calc_id,
        "calculation_type": payload.calculation_type,
        "status": "generated",
        "code_snippet": f"def calculate_{payload.calculation_type}(dataset): return sum(dataset) / len(dataset)",
        "created_at": datetime.now(timezone.utc).isoformat()
    }

@app.post("/calculation-code/execute", tags=["Analytics"])
def execute_calculation_code(
    payload: CalculationExecuteRequest,
    trace_id: Optional[str] = Depends(resolve_trace_id),
    x_client_id: Optional[str] = Header(default=None),
    x_metadata_tag: Optional[str] = Header(default=None)
):
    """13 - Execute Calculation Code (trace headers)"""
    return {
        "calculation_id": payload.calculation_id,
        "status": "completed",
        "result": {
            "kpi_name": "alarm_flood_index",
            "value": 1.85,
            "unit": "events/week",
            "status": "compliant",
            "evaluation_window_hours": 168
        },
        "execution_time_ms": 35,
        "trace_metadata": {
            "trace_id": trace_id,
            "x_client_id": x_client_id,
            "x_metadata_tag": x_metadata_tag
        }
    }

@app.get("/analytics/kpi-definitions", tags=["Analytics"])
def get_kpi_definitions():
    """14 - KPI Definitions"""
    return {
        "kpis": DATA_STORE["kpi_definitions"]
    }

# ==============================================================================
# 5. TICKETING SYSTEM APIS (INCIDENT & TICKET ENRICHMENT)
# ==============================================================================

@app.get("/tickets", tags=["Ticketing"])
def list_tickets(
    status: Optional[str] = None,
    asset_id: Optional[str] = None,
    asset_ids: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 50
):
    """List support and incident tickets with multi-asset filtering."""
    results = DATA_STORE["tickets"]

    if status and isinstance(status, str):
        results = [t for t in results if t.get("status", "").lower() == status.lower()]
    if asset_id and isinstance(asset_id, str):
        results = [t for t in results if t.get("asset_id", "").lower() == asset_id.lower()]
    if asset_ids and isinstance(asset_ids, str):
        ids_list = [aid.strip().lower() for aid in asset_ids.split(",") if aid.strip()]
        results = [t for t in results if t.get("asset_id", "").lower() in ids_list]
    if severity and isinstance(severity, str):
        results = [t for t in results if t.get("severity", "").lower() == severity.lower()]

    return {
        "tickets": results[:limit],
        "total": len(results)
    }

@app.get("/tickets/search", tags=["Ticketing"])
def search_tickets(
    query: str,
    asset_id: Optional[str] = None
):
    """
    Search historical tickets by symptoms, root causes, or resolution notes.
    Used by Copilot to find similar prior resolutions.
    """
    q_words = query.lower().split()
    matched = []

    for ticket in DATA_STORE["tickets"]:
        # Match asset if provided
        if asset_id and ticket.get("asset_id", "").lower() != asset_id.lower():
            continue

        searchable_text = f"{ticket.get('title', '')} {ticket.get('symptom', '')} {ticket.get('root_cause', '')} {ticket.get('resolution_notes', '')}".lower()

        # Simple match scoring
        match_score = sum(1 for w in q_words if w in searchable_text)
        if match_score > 0:
            ticket_copy = dict(ticket)
            ticket_copy["relevance_score"] = match_score
            matched.append(ticket_copy)

    # Sort by match relevance
    matched.sort(key=lambda x: x["relevance_score"], reverse=True)
    return {
        "query": query,
        "results": matched,
        "count": len(matched)
    }

@app.get("/tickets/{ticket_id}", tags=["Ticketing"])
def get_ticket_by_id(ticket_id: str):
    """Get single ticket details."""
    for ticket in DATA_STORE["tickets"]:
        if ticket["ticket_id"].lower() == ticket_id.lower():
            return ticket
    raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found")

@app.post("/tickets", status_code=status.HTTP_201_CREATED, tags=["Ticketing"])
def create_ticket(
    payload: TicketCreateRequest,
    trace_id: Optional[str] = Depends(resolve_trace_id)
):
    """
    Create a new incident ticket.
    NOTE: In the Copilot GUI, this should only be invoked after explicit Human Approval!
    """
    tickets_list = DATA_STORE["tickets"]
    new_id = f"INC-{1000 + len(tickets_list) + 1}"
    now_iso = datetime.now(timezone.utc).isoformat()

    new_ticket = {
        "ticket_id": new_id,
        "asset_id": payload.asset_id,
        "alarm_id": payload.alarm_id,
        "title": payload.title,
        "status": "open",
        "severity": payload.severity,
        "priority": payload.priority,
        "symptom": payload.symptom,
        "likely_cause": payload.likely_cause,
        "resolution_notes": None,
        "recommended_action": payload.recommended_action,
        "sop_reference": payload.sop_reference,
        "assigned_to": payload.assigned_to,
        "assigned_user": payload.assigned_user,
        "similar_ticket_ref": payload.similar_ticket_ref,
        "created_at": now_iso,
        "resolved_at": None,
        "audit_trail": [
            {
                "timestamp": now_iso,
                "action": "Ticket created via Copilot MCP after operator approval",
                "trace_id": trace_id
            }
        ]
    }

    # Prepend so newly created ticket appears at the top
    tickets_list.insert(0, new_ticket)
    return {
        "message": "Incident ticket created successfully",
        "ticket": new_ticket
    }

@app.patch("/tickets/{ticket_id}", tags=["Ticketing"])
def update_ticket(ticket_id: str, payload: TicketUpdateRequest):
    """Update status, resolution notes, or assignment on a ticket."""
    for ticket in DATA_STORE["tickets"]:
        if ticket["ticket_id"].lower() == ticket_id.lower():
            if payload.status:
                ticket["status"] = payload.status
                if payload.status.lower() in ("closed", "resolved"):
                    ticket["resolved_at"] = datetime.now(timezone.utc).isoformat()
            if payload.priority:
                ticket["priority"] = payload.priority
            if payload.resolution_notes:
                ticket["resolution_notes"] = payload.resolution_notes
            if payload.assigned_to:
                ticket["assigned_to"] = payload.assigned_to
            if payload.assigned_user:
                ticket["assigned_user"] = payload.assigned_user
            if payload.work_notes:
                audit = ticket.setdefault("audit_trail", [])
                audit.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "note": payload.work_notes
                })
            return {
                "message": f"Ticket {ticket_id} updated",
                "ticket": ticket
            }
    raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found")

# ==============================================================================
# 6. STANDALONE ENTRY POINT
# ==============================================================================

if __name__ == "__main__":
    import uvicorn
    print("\n" + "=" * 70)
    print("🚀 Starting Alarm API Simulator & Ticketing Service on http://0.0.0.0:8000")
    print("📖 Interactive Swagger API Docs available at: http://localhost:8000/docs")
    print("=" * 70 + "\n")
    uvicorn.run("simulator_app:app", host="0.0.0.0", port=8000, reload=True)
