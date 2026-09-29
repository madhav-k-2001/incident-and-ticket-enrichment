# Standard Operating Procedure: SOP-MTR-501
**Document ID:** SOP-OPS-2026-MTR501-03  
**Title:** Compressor Drive Motor 501 Vibration Surveillance and Operating Limits  
**Target Asset:** `M-501` (Compressor Drive Motor 501 – 6.6 kV ABB)  
**Coupled Asset:** `CMP-201` (Wet Gas Compressor 201)  
**Location:** EastRefinery – Compressor Substation Unit 5 / Unit 2  
**Associated Alarms:** `MTR-VIB-HI`  
**Classification:** Standard Reliability & Maintenance Procedure  
**Effective Date:** March 1, 2026  
**Revision:** 2.0  

---

## 1. Vibration Severity Guidelines (ISO 10816-3 Class IV)

| Velocity RMS (Overall) | Zone / Status | Operating Action Required |
|---|---|---|
| **0.0 – 4.5 mm/s** | Zone A: Newly Commissioned | Normal continuous operation |
| **4.5 – 6.5 mm/s** | Zone B: Acceptable Operation | Standard routine weekly monitoring |
| **6.5 – 9.3 mm/s** | Zone C: Restricted Operation | Trigger Alarm `MTR-VIB-HI`. Detailed diagnostic evaluation within 24 hours. |
| **9.3 – 11.2 mm/s** | Zone C/D Boundary: Hazardous | Prepare controlled motor shutdown. Notify Shift Superintendent. |
| **> 11.2 mm/s** | Zone D: Dangerous (Trip Limit) | Automatic protection trip initiates within 2.0 seconds. |

---

## 2. Aerodynamic Coupling and Correlated Failure Modes

Operational telemetry demonstrates strong physical correlation between Compressor `CMP-201` and Motor `M-501`:
1. When `CMP-201` experiences high discharge pressure (> 45.0 bar), rotating stall and aerodynamic shockwaves transmit axial cyclic forces through the flexible diaphragm coupling (`02-CP-201`).
2. This backpressure buffeting induces premature bearing micro-vibration on motor inboard bearing sensor `05-VE-501A`.
3. **Correlation Window:** Peak motor vibration spikes lag compressor discharge pressure surges by **2.5 to 4.0 minutes**.

---

## 3. Operator Response Actions

When alarm `MTR-VIB-HI` triggers (> 6.5 mm/s):
1. **Check Compressor State:**
   - If `CMP-201` discharge pressure is concurrently alarming or elevated (> 42.0 bar), the primary root cause is aerodynamic disturbance rather than mechanical motor bearing failure.
   - Mitigate compressor pressure first via `SOP-CMP-201`. Motor vibration typically attenuates below 6.0 mm/s once compressor pressure returns to nominal.
2. **Local Bearing Temperature Inspection:**
   - If motor vibration is elevated while compressor pressure is stable (< 38.0 bar), dispatch technician with infrared pyrometer.
   - Bearing housing surface temperature must remain below 75°C.
3. **Acoustic Lubrication Check:**
   - Use high-frequency acoustic stethoscope (e.g. Ultraprobe 9000). A high decibel reading (> 35 dBµV) indicates grease starvation. Apply synthetic Polyrex EM grease (maximum 25 grams).

---

## 4. Ticketing & Consolidation Governance

- An active ticket already exists for ongoing motor vibration tracking: **`INC-1188`**.
- If a new incident arises during a combined compressor-motor event, do not create duplicate isolated tickets for `M-501`.
- Instead, draft the primary incident under `CMP-201` and link `INC-1188` as a correlated dependent ticket.
