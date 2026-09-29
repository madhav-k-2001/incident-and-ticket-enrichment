# Technical Troubleshooting Guide: TSG-PMP-101
**Document ID:** TSG-ENG-2026-BFP-05  
**Title:** Boiler Feed Pump 101 Lube Oil Circuit Diagnostics & Heat Exchanger Maintenance  
**Target Asset:** `BFP-101` (Sulzer GSG 80-260 Multi-Stage Pump)  
**Location:** EastRefinery – Utilities Unit 1  
**Discipline:** Mechanical Maintenance & Reliability Engineering  
**Classification:** Rotating Equipment Technical Guide  
**Last Revised:** April 10, 2026  

---

## 1. System Architecture & Common Abnormalities

The lube oil system supplies ISO VG 46 synthetic turbine oil to inboard and outboard hydrodynamic sleeve bearings, as well as the Kingsbury double-acting tilting-pad thrust bearing.

### Primary Symptom Pathways:

1. **High Bearing Temperature with Normal Oil Pressure:**
   - *Root Cause:* Plate-and-frame cooler heat exchanger (`HE-101A/B`) biological fouling or calcium carbonate scaling on the cooling water side.
   - *Signature:* Oil return temperature exceeds 65°C while cooling water supply temperature remains normal (< 28°C).
   - *Remediation:* Citric acid chemical recirculation wash as detailed in `KB-BFP-101-Plate-Cooler-Descaling.md`. Reference resolved incident `INC-0955`.

2. **Low Lube Oil Pressure with Temperature Spike:**
   - *Root Cause:* Main shaft-driven oil pump discharge relief valve stuck partially open, or auxiliary pump cut-in delay.
   - *Signature:* Oil pressure drops below 1.5 bar (`BFP-LUBE-OIL-P-LO`), immediately followed by a rapid rise in thrust bearing temperature within 3 to 5 minutes.
   - *Remediation:* Switch to auxiliary electric pump. Check thermal overload relay in MCC panel Unit 1 (reference historical case `INC-0710`).

3. **High Differential Pressure Across Duplex Filters:**
   - *Root Cause:* Particulate contamination or varnish accumulation.
   - *Action Limit:* Filter DP > 0.6 bar requires manual switchover to standby basket and replacement of 10-micron microglass filter elements.

---

## 2. Chemical Flushing & Cooler Descaling Procedure

When cooler performance falls below heat transfer coefficient $U = 850 \text{ W/m}^2\text{K}$:
1. Isolate cooler bundle `HE-101A` using block valves.
2. Hook up portable acid cleaning skid with 5% inhibited citric acid solution heated to 50°C.
3. Circulate solution in reverse flow direction for 4 hours.
4. Neutralize with 0.5% sodium carbonate flush and rinse thoroughly with demineralized water.
5. Record clean differential pressure across cooler bundle.

---

## 3. Oil Quality Acceptance Standards

Oil samples must be extracted every 30 days and analyzed according to ASTM standards:
- **Kinematic Viscosity (40°C):** 41.4 – 50.6 cSt (ASTM D445)
- **Water Content:** < 200 ppm (Karl Fischer ASTM D6304). If > 500 ppm, initiate vacuum dehydration immediately.
- **Particle Count:** ISO 4406 Cleanliness Code 16/14/11 maximum.
- **Total Acid Number (TAN):** < 0.20 mg KOH/g.
