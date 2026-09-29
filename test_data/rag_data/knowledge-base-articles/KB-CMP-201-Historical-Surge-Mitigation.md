# Knowledge Base Article: KB-I&C-1042
**Article ID:** KB-ROT-2026-CMP-07  
**Title:** Case Study & Root Cause Analysis: Compressor 201 Discharge Pressure Excursion and Surge Mitigation  
**Target Asset:** `CMP-201` (Wet Gas Compressor 201)  
**Related Incident Records:** `INC-1042`, `INC-0891`  
**Discipline:** Rotating Equipment & Plant Automation  
**Author:** Sarah Jenkins, Lead Instrumentation & Controls Engineer  
**Approved By:** Marcus Vance, Chief Reliability Engineer  
**Published Date:** March 20, 2026  

---

## 1. Executive Summary: Incident INC-1042 (March 14, 2026)

On March 14, 2026 at 09:20 UTC, Wet Gas Compressor `CMP-201` experienced a sudden rise in 2nd stage discharge pressure to **46.8 bar** during a routine unit feed transition. Alarm `CMP-DISCH-P-HI` triggered immediately. 

The DCS anti-surge controller `FIC-201` commanded anti-surge recycle valve `02-FV-201` to open to 65%. However, field telemetry indicated that discharge pressure continued rising towards the emergency shutdown trip limit (50.0 bar), and driver motor `M-501` experienced an inboard bearing vibration jump to 7.6 mm/s.

### Emergency Field Intervention:
The board operator placed controller `FIC-201` in manual override and commanded 100% stroke while dispatching an outside operator. The outside operator tapped the local valve positioner enclosure and manually actuated the hand jack, preventing an automatic ESD trip.

---

## 2. Root Cause Analysis Findings

Upon detailed post-incident teardown of the actuation assembly on valve `02-FV-201`:
1. **Mechanical Looseness in Feedback Arm:**
   - The DVC6200 positioner feedback lever clamp screw was torqued to only 3.2 Nm (manufacturer specification: 8.5 Nm).
   - High-frequency process acoustic vibration from the 10-inch bypass line had backed the clamp screw out by 1.5 turns.
   - Consequently, the positioner internal Hall-effect sensor perceived the valve as 65% open, while the physical plug was mechanically pinned at approximately 14% travel.
2. **Instrument Air Quality:**
   - Air supply regulator PI-2014 was found with minor moisture accumulation in the drip bowl, contributing to slight pilot valve sluggishness.

---

## 3. Corrective Actions & Preventative Measures Implemented

To eliminate recurrence of this failure mode:
- **Fastener Locking:** All clamp screws on anti-surge valve feedback linkages across Unit 2 were replaced with grade 316 stainless steel cap screws and secured with Loctite 243 threadlocker.
- **Dynamic Response Verification:** Automated valve signature stroke tests must be scheduled quarterly using the AMS Trex diagnostic tool.
- **DCS Travel Discrepancy Alarm:** Implemented a software cross-check alarm (`CMP-VLV-DEV-ALM`) in the DCS that triggers if positioner feedback deviates from DCS command by more than 5% for longer than 3 seconds.

---

## 4. Key Takeaways for Operating & Support Crews

When troubleshooting rapid overpressure events on `CMP-201`:
1. **Always cross-check physical valve position against DCS command.** Do not trust the DCS position readout alone if pressure is continuing to rise.
2. **Look for correlated motor vibration.** Motor `M-501` vibration is a reliable acoustic and aerodynamic barometer of compressor internal flow stability.
3. Reference `INC-1042` in any work order involving valve `02-FV-201` to ensure technicians verify linkage torque before suspecting internal valve trim damage.
