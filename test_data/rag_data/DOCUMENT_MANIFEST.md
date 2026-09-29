# Document Corpus Catalog & RAG Ingestion Manifest

This folder contains the authoritative document corpus for the **Incident and Ticket Enrichment Copilot**. The documents represent standard operating procedures, technical troubleshooting guides, plant escalation policies, historical case studies, and safety instructions for East Refinery.

---

## Corpus Overview

| Document ID | Filename | Document Type | Target Asset(s) | Triggering Alarm(s) | Primary Purpose / Summary |
|---|---|---|---|---|---|
| `SOP-OPS-2026-CMP201-01` | `SOP-CMP-201-Discharge-Overpressure.md` | Standard Operating Procedure (SOP) | `CMP-201`, `M-501` | `CMP-DISCH-P-HI`, `CMP-SURGE-WARN` | Immediate operator response steps, setpoints (42 bar warn, 50 bar ESD trip), manual override of anti-surge valve `02-FV-201`. |
| `SOP-OPS-2026-BFP101-02` | `SOP-BFP-101-Thrust-Bearing-Protection.md` | Standard Operating Procedure (SOP) | `BFP-101`, `BFP-102` | `BFP-BEAR-TEMP-HI`, `BFP-LUBE-OIL-P-LO` | Bearing temperature trip thresholds (80°C warn, 95°C trip), standby pump switchover, 90-day recurrence mitigation. |
| `SOP-OPS-2026-MTR501-03` | `SOP-MTR-501-Vibration-Mitigation.md` | Standard Operating Procedure (SOP) | `M-501`, `CMP-201` | `MTR-VIB-HI` | Motor vibration severity zones (ISO 10816-3), aerodynamic coupling with compressor discharge pressure, link to `INC-1188`. |
| `TSG-ENG-2026-VALVE-04` | `TSG-CMP-AntiSurge-Valve-Diagnostics.md` | Technical Troubleshooting Guide (TSG) | `CMP-201` (`02-FV-201`) | `CMP-DISCH-P-HI` | Deep diagnostics on Fisher DVC6200 positioner, pneumatic actuator leak testing, feedback linkage alignment, torque specs (8.5 Nm). |
| `TSG-ENG-2026-BFP-05` | `TSG-BFP-LubeOil-System-Diagnostics.md` | Technical Troubleshooting Guide (TSG) | `BFP-101` | `BFP-BEAR-TEMP-HI`, `BFP-LUBE-OIL-P-LO` | Lube oil circuit troubleshooting, plate cooler fouling diagnostics, ASTM oil quality standards, citric acid descaling wash. |
| `GOV-SLA-2026-INC-01` | `ESCALATION-EastRefinery-Incident-Matrix.md` | Governance & Escalation Policy | All Assets | All Critical / High Alarms | SLA response commitments (P1 15-min SLA), notification tree, mandatory ticket fields, and Human-in-the-Loop write policy. |
| `KB-ROT-2026-CMP-07` | `KB-CMP-201-Historical-Surge-Mitigation.md` | Support Knowledge Base (KB) | `CMP-201` | `CMP-DISCH-P-HI` | Post-incident case study of `INC-1042` and `INC-0891`. Root cause analysis on mechanical linkage loosening under vibration. |
| `KB-ENG-2026-BFP-08` | `KB-BFP-101-Plate-Cooler-Descaling.md` | Support Knowledge Base (KB) | `BFP-101` | `BFP-BEAR-TEMP-HI` | Lessons learned from `INC-0955` and `INC-0710`. Procedure for chemical descaling and 75-day preventative cleaning policy. |
| `SAF-HSE-2026-CH2-09` | `SAF-02-Compressor-Hall-Personal-Safety.md` | Safety & Environmental (HSE) | `CMP-201`, `CMP-202`, `M-501` | Gas Detection, LEL, H2S | Mandatory PPE, dual hearing protection, gas sensor threshold limits, LOTO electrical lockout, prompt-injection defense guidelines. |

---

## Citation Formatting Reference for Copilot RAG

When generating grounded answers and incident drafts, the Copilot should construct citations following this standard schema:

### Example Citation Formats:
* **Operating Procedure:**  
  `[Source: SOP-CMP-201-Discharge-Overpressure.md, Section 2.1 (Lines 22-38)]`
* **Knowledge Article:**  
  `[Source: KB-CMP-201-Historical-Surge-Mitigation.md, Section 2 (Lines 28-44)]`
* **Escalation Policy:**  
  `[Source: ESCALATION-EastRefinery-Incident-Matrix.md, Section 3 (Lines 48-65)]`

---

## Ingestion Metadata Schema

Every document in this directory is chunked using standard markdown section boundaries (`##`, `###`) with the following metadata tags:

```json
{
  "doc_id": "SOP-OPS-2026-CMP201-01",
  "filename": "SOP-CMP-201-Discharge-Overpressure.md",
  "title": "Response to High Discharge Pressure and Surge Proximity on Wet Gas Compressor 201",
  "doc_type": "standard_operating_procedure",
  "site": "EastRefinery",
  "unit": "Unit 2",
  "primary_asset": "CMP-201",
  "correlated_assets": ["M-501", "CMP-202"],
  "alarm_codes": ["CMP-DISCH-P-HI", "CMP-SURGE-WARN"],
  "safety_critical": true,
  "chunk_id": "SOP-CMP-201-chunk-02",
  "section": "2. Immediate Operator Actions"
}
```
