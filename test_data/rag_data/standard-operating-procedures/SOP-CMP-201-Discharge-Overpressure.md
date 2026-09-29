# Standard Operating Procedure: SOP-CMP-201
**Document ID:** SOP-OPS-2026-CMP201-01  
**Title:** Response to High Discharge Pressure and Surge Proximity on Wet Gas Compressor 201  
**Target Asset:** `CMP-201` (Wet Gas Compressor 201)  
**Location:** EastRefinery – Process Unit 2  
**Associated P&ID Tag:** `02-CMP-201-K`  
**Associated Alarms:** `CMP-DISCH-P-HI`, `CMP-SURGE-WARN`  
**Classification:** Tier 1 Critical Safety Procedure  
**Effective Date:** January 15, 2026  
**Revision:** 4.2  

---

## 1. Operating Thresholds and Setpoints

| Parameter | Normal Range | Advisory Alarm | High Warning | Critical Action Limit | Emergency Trip (ESD-2) |
|---|---|---|---|---|---|
| 2nd Stage Discharge Pressure | 34.0 – 40.0 bar | 40.5 bar | 42.0 bar (`CMP-DISCH-P-HI`) | 48.0 bar | 50.0 bar |
| Anti-Surge Control Margin | > 1.25 ratio | 1.20 ratio | 1.15 ratio | 1.10 ratio (`CMP-SURGE-WARN`) | < 1.02 ratio |
| 2nd Stage Discharge Temp | 95 – 115°C | 120°C | 125°C | 130°C | 135°C |
| Recycle Valve Opening (`02-FV-201`) | 0% (Closed) | Modulating (Auto) | > 30% Auto | 100% Full Open | Auto Trip on Fail to Open |

---

## 2. Immediate Operator Actions (Step-by-Step)

When alarm `CMP-DISCH-P-HI` triggers (> 42.0 bar) or active telemetry indicates rising pressure:

### Step 2.1: Verify Anti-Surge Controller Status
1. Check the Distributed Control System (DCS) console for controller `FIC-201`.
2. Verify if the control valve `02-FV-201` output demand matches the physical stem feedback.
3. If `02-FV-201` indicates < 20% opening while pressure exceeds 45.0 bar:
   - **Immediately switch controller FIC-201 from AUTO to MANUAL mode.**
   - Ramp manual output demand to **25% open stroke** in increments of 5%.
   - Observe if discharge pressure stabilizes or decreases below 42.0 bar within 90 seconds.

### Step 2.2: Field Dispatch and Local Verification
1. Dispatch an outside plant operator to Compressor Hall 2 immediately.
2. Verify instrument air supply pressure at regulator PI-2014 (minimum required: 5.5 bar gauge).
3. Visually inspect the actuator mechanical stem and positioner feedback linkage on valve `02-FV-201`.
4. Inspect the manual isolation bypass valve `02-V-119` downstream of the recycle cooler; ensure the valve handwheel is locked in the 100% car-sealed open position.

### Step 2.3: Inter-Asset Safeguard Actions
1. Check the vibration status on primary driver motor `M-501`. Excessive backpressure causes aerodynamic buffeting that manifests as high-frequency vibration on motor bearing sensors.
2. If discharge pressure reaches **49.0 bar** and anti-surge response fails:
   - **Do NOT await automated ESD trip.**
   - Manually depress the Emergency Unit Stop push-button on DCS panel Unit 2 (`ESD-2-PB-01`).
   - Initiate nitrogen purge sequence across compressor seal chambers.

---

## 3. Incident Logging and Ticketing Guidelines

Under East Refinery operational governance, an incident ticket must be logged within **15 minutes** of any `CMP-DISCH-P-HI` activation exceeding 45.0 bar:

1. **Mandatory Ticket Categorization:**
   - **Service / Department:** Instrumentation & Controls (I&C) + Rotating Machinery
   - **Priority:** P1 – Critical (if > 47.0 bar) or P2 – High (if between 42.0 and 47.0 bar)
   - **Affected Primary Asset:** `CMP-201`
   - **Correlated Secondary Asset:** `M-501`
2. **Required Ticket Details:**
   - Peak discharge pressure reached (`bar`).
   - Anti-surge valve `02-FV-201` position feedback (%) at time of alarm.
   - Reference previous resolution case `INC-1042` to ensure technicians check positioner linkage tightness prior to replacement.
   - Attach reference to `SOP-CMP-201 §2`.

---

## 4. Post-Incident Inspection Checklist

Before authorizing compressor restart following an overpressure excursion:
- [ ] Perform 0% to 100% full stroke travel test on anti-surge valve `02-FV-201`.
- [ ] Inspect pneumatic tubing for moisture contamination or pinhole leaks.
- [ ] Verify calibration of pressure transmitters `02-PT-2014A` and `02-PT-2014B` (2-out-of-3 voting logic).
- [ ] Conduct vibration phase spectrum test on drive motor `M-501`.
- [ ] Obtain written restart approval from the Unit 2 Shift Superintendent.
