# Plant Governance Procedure: ESCALATION-EAST-01
**Document ID:** GOV-SLA-2026-INC-01  
**Title:** East Refinery Incident Prioritization, Escalation Matrix & Ticketing Protocol  
**Scope:** EastRefinery – All Operating Units (Unit 1, Unit 2, Unit 3, Unit 5)  
**Governance Authority:** Plant Operations & Operational Technology (OT) Steering Committee  
**Classification:** Refinery Policy Document  
**Effective Date:** January 1, 2026  
**Revision:** 5.0  

---

## 1. Incident Severity Definitions & SLA Commitments

Every event requiring maintenance, engineering intervention, or operational investigation must be assigned an Incident Severity Level:

| Severity Level | Operational Criteria | Maximum Response SLA | Target Resolution SLA | Mandatory Approvers |
|---|---|---|---|---|
| **P1 – Critical** | - Process safety trip imminent or tripped.<br>- Primary production unit throughput reduction > 30%.<br>- Critical asset alarm active (e.g. `CMP-DISCH-P-HI` > 47 bar, `BFP-BEAR-TEMP-HI` > 90°C). | **15 minutes** | **4 hours** | Shift Superintendent & Lead Reliability Engineer |
| **P2 – High** | - Unit operating under restricted parameter limits.<br>- Redundant equipment running with standby unavailable.<br>- Recurring alarm frequency > 3 occurrences in 7 days. | **45 minutes** | **12 hours** | Area Operations Supervisor |
| **P3 – Medium** | - Degradation observed with negligible immediate production risk.<br>- Advisory alarm persisting (e.g. `MTR-VIB-HI` in Zone C). | **4 hours** | **48 hours** | Discipline Maintenance Planner |
| **P4 – Low / Routine** | - Cosmetic gauge issue, non-critical sensor drift, or minor housekeeping. | **24 hours** | **7 days** | Operations Support Engineer |

---

## 2. Cross-Functional Notification & Escalation Tree

```
                                  [ALARM DETECTED / INCIDENT DRAFTED]
                                                   │
                                ┌──────────────────┴──────────────────┐
                         P1 - CRITICAL                         P2 - HIGH
                                │                                     │
                  ┌─────────────┴─────────────┐                 ┌─────┴─────┐
                  ▼                           ▼                 ▼           ▼
        [Shift Superintendent]    [Discipline Leads]      [Area Supv]  [Lead Tech]
                  │              (Rotating/I&C/Elec)            │
                  ▼                           │                 ▼
        [Plant General Mgr] ◄─────────────────┘           [Planner Assigned]
        (if > 2 hr outage)
```

### Escalation Thresholds:
- **P1 Incident Escalation:** If a P1 incident remains unresolved at **2 hours**, the Operations Superintendent must convene an emergency Technical Response Team (TRT) and notify the Plant Operations Director.
- **Vendor Technical Assistance:** If initial diagnostics on anti-surge valves or compressor rotating elements fail within 3 hours, vendor service contracts (e.g., Siemens Energy Remote Support, Flowserve Quick-Response Center) must be engaged.

---

## 3. Mandatory Incident Ticket Fields

To ensure full regulatory compliance, root cause tracking, and AI Copilot auditability, every created ticket must contain:

1. **Ticket Title:** Standard format: `[SEVERITY] [Asset ID] - [Alarm Code / Symptom Description]`  
   *Example:* `[CRITICAL] CMP-201 - 2nd Stage Discharge Overpressure Exceeded (48.2 bar)`
2. **Primary Asset ID:** Canonical asset identifier matching the refinery asset registry (`CMP-201`, `BFP-101`, `M-501`).
3. **Triggering Alarm ID:** Unique alarm instance identifier (`ALM-9021`).
4. **Current Parameter Value vs Threshold:** Exact engineering values (`48.2 bar vs 42.0 bar limit`).
5. **Initial Root Cause Hypothesis:** Plausible contributing mechanical, electrical, or process factors.
6. **Immediate Mitigation Action:** Actions executed by board or field operators per approved SOP.
7. **Referenced Standard Operating Procedure (SOP):** Document ID and specific section reference (e.g., `SOP-CMP-201 §2.1`).
8. **Historical Similar Case Link:** Cross-reference to previous similar resolved tickets (e.g. `INC-1042`).

---

## 4. Human-in-the-Loop (HITL) Gate Policy for Autonomous Tools

In accordance with East Refinery Cybersecurity and Operational Safety Standards:
- **Autonomous Write Prohibition:** AI copilots, robotic process automations, and external integrations are strictly **prohibited from creating, modifying, or closing tickets in production CMMS/ticketing systems without explicit human operator confirmation.**
- **Approval Gate:** The copilot may prepare, structure, enrich, and draft the ticket. The shift board operator or reliability engineer must review the draft in the graphical interface, modify fields if required, and click the explicit confirmation button before the ticket creation API is called.
- **Trace Audit Requirement:** Every created incident must record the `trace_id` generated during the copilot session.
