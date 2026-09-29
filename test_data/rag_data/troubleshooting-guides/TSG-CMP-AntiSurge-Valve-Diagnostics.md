# Technical Troubleshooting Guide: TSG-I&C-201
**Document ID:** TSG-ENG-2026-VALVE-04  
**Title:** Comprehensive Diagnostic Guide: Anti-Surge Recycle Valve Actuation & Positioner Alignment  
**Target Subsystem:** Valve Tag `02-FV-201` (Flowserve Kammer 200000 Series with Fisher DVC6200 Positioner)  
**Parent Asset:** `CMP-201` (Wet Gas Compressor 201)  
**Discipline:** Instrumentation, Controls, and Valve Reliability  
**Classification:** Engineering Field Manual  
**Last Revised:** February 18, 2026  

---

## 1. Physical Description & Failure Modes

Anti-surge valve `02-FV-201` protects `CMP-201` from catastrophic aerodynamic surge by recirculating compressed wet gas back to suction cooler inlet. The valve is designed to achieve **98% full open stroke within 850 milliseconds** upon emergency trip command.

### Top Failure Modes Observed at East Refinery:
1. **Mechanical Feedback Linkage Slippage:**
   - The feedback arm coupling between the valve stem clamp and the digital valve controller (DVC6200) position sensor loosens due to continuous process piping vibration.
   - *Symptom:* DCS reads 45% valve open, but physical valve plug is only 15% open, causing discharge pressure to spike above 46.0 bar.
   - *Confirmed Case:* Incident `INC-1042`.
2. **Pneumatic Actuator Supply Depletion:**
   - Supply regulator PI-2014 drops below required 5.5 bar supply pressure, leading to sluggish opening times or valve hunting.
3. **Pneumatic Booster Relay Diaphragm Rupture:**
   - High-flow volume boosters (e.g. Fisher 2625) suffer diaphragm fatigue, causing localized instrument air venting and inability to sustain high-speed opening.
4. **Stem Packing Overtightening / Binding:**
   - Severe stem friction (> 1.5 kN above specification) prevents smooth modulation, creating a 3% to 5% stick-slip deadband.

---

## 2. Step-by-Step Field Diagnostic Procedure

When investigating sluggish response or uncommanded pressure rise:

```
[Is Instrument Air Pressure >= 5.5 bar?]
    ├── NO  ──► Inspect filter-regulator PI-2014. Drain moisture. Replace clogged filter element.
    └── YES ──► Proceed to Feedback Linkage Inspection
                     │
[Is Feedback Arm Alignment Rigid?]
    ├── NO  ──► Tighten socket head cap screws to 8.5 Nm torque. Perform 0-100% calibration.
    └── YES ──► Proceed to Valve Signature Diagnostic
```

### Step 2.1: Mechanical Feedback Alignment Check
1. Power down digital loop supply to positioner.
2. Inspect the magnetic array and feedback pin on the DVC6200 positioner.
3. Verify that the alignment line on the feedback arm matches the horizontal centerline of the actuator travel indicator at 50% stroke.
4. If loose, torque the M6 clamping screw to **8.5 Nm**. Apply Loctite 243 threadlocker to prevent vibration-induced loosening.

### Step 2.2: Valve Signature & Stroke Time Verification
1. Connect Emerson AMS Trex Device Communicator to the HART terminals.
2. Run automated diagnostic routine: `HART -> Diagnostics -> Valve Signature`.
3. Check dynamic error band:
   - Dynamic error band must be **< 1.2%**.
   - If dynamic error exceeds 3.0%, pack friction or spring hysteresis is present.
4. Measure full open trip response time:
   - Command 100% open from 0% steady state.
   - Time to reach 90% travel must not exceed **1.0 second**.

---

## 3. Recommended Incident Remediation Notes

When closing out an incident ticket involving `02-FV-201`:
- Document exact torque values applied to stem clamps.
- Record pre-calibration and post-calibration stroke travel feedback (%).
- Attach before-and-after Valve Signature charts to the incident ticket audit record.
