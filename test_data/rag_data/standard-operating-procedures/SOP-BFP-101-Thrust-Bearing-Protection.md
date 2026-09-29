# Standard Operating Procedure: SOP-BFP-101
**Document ID:** SOP-OPS-2026-BFP101-02  
**Title:** Boiler Feed Pump 101 Thrust Bearing Overheat & Lube Oil Protection  
**Target Asset:** `BFP-101` (Boiler Feed Pump 101)  
**Backup Asset:** `BFP-102` (Boiler Feed Pump 102 – Standby)  
**Location:** EastRefinery – Utilities & Steam Unit 1  
**Associated Alarms:** `BFP-BEAR-TEMP-HI`, `BFP-LUBE-OIL-P-LO`  
**Classification:** Tier 1 Critical Plant Machinery Procedure  
**Effective Date:** February 1, 2026  
**Revision:** 3.1  

---

## 1. Safety Thresholds & Alarm Limits

| Parameter | Normal Value | Advisory Limit | Alarm Trigger (`HIGH`) | Emergency Trip (`CRITICAL`) |
|---|---|---|---|---|
| Inboard Thrust Bearing Temp | 60.0 – 72.0°C | 76.0°C | 80.0°C (`BFP-BEAR-TEMP-HI`) | 95.0°C |
| Outboard Journal Bearing Temp | 55.0 – 68.0°C | 72.0°C | 78.0°C | 90.0°C |
| Main Lube Oil Header Pressure | 2.2 – 2.8 bar | 1.9 bar | 1.5 bar (`BFP-LUBE-OIL-P-LO`) | < 1.0 bar |
| Cooling Water Supply Temp | 24.0 – 28.0°C | 32.0°C | 35.0°C | N/A |

---

## 2. Immediate Operator Response Protocol

### Step 2.1: Initial Triage on Bearing Temperature High (> 80.0°C)
1. Verify if the temperature rise is isolated to a single thermocouple (e.g., `TE-1014A`) or verified across dual sensors (`TE-1014A` and `TE-1014B`).
2. Check lube oil supply pressure upstream and downstream of the duplex lube oil filter.
3. If differential pressure across the oil filter exceeds **0.6 bar**:
   - Equalize pressure on the standby filter basket using the balance cock.
   - Switch the duplex filter three-way valve handle smoothly to basket B.
4. Check the cooling water delta-T across the lube oil plate heat exchanger (`HE-101`). If delta-T is < 2.0°C while oil temperature exceeds 75°C, plate fouling is indicated.

### Step 2.2: Mitigation of Recurring Bearing Alarms (90-Day Policy)
Historical records show recurring thermal degradation on `BFP-101` during high ambient summer months:
- If `BFP-BEAR-TEMP-HI` has triggered more than **3 times within a 30-day window**:
  - The board operator must switch the service to standby pump `BFP-102`.
  - Open an urgent work order for heat exchanger descaling (reference historical case `INC-0955`).
  - Do not allow continuous operation above 85°C for longer than 60 minutes.

### Step 2.3: Response to Lube Oil Pressure Dip (< 1.5 bar)
1. Ensure auxiliary electric lube oil pump `01-P-101-AUX` has auto-started on low pressure detection.
2. If the auxiliary pump fails to cut in automatically:
   - Manually turn the control switch on the Unit 1 MCC panel to `MANUAL-START`.
   - If header pressure does not recover to > 1.8 bar within 30 seconds, manually trip `BFP-101` to prevent babbit bearing wipe.

---

## 3. Ticketing & Escalation Governance

* **Severity:** P2 (High) if temperature is between 80.0°C and 88.0°C. Escalate to P1 (Critical) if temperature exceeds 88.0°C or if lube oil pressure dips below 1.4 bar.
* **Assigned Department:** Boiler House Mechanical Operations & Rotating Reliability.
* **Mandatory Ticket Contents:**
  - Running hours since last bearing inspection.
  - Current vibration levels (axial and radial).
  - Oil sample laboratory status (viscosity, water content in ppm, particle count).
  - Note: Link ticket to historical corrective action `INC-0955` and electrical repair `INC-0710`.
