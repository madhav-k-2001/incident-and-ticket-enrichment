# Knowledge Base Article: KB-UTL-0955
**Article ID:** KB-ENG-2026-BFP-08  
**Title:** Lessons Learned & Corrective Procedures: Boiler Feed Pump 101 Lube Oil Heat Exchanger Scaling  
**Target Asset:** `BFP-101` (Boiler Feed Pump 101)  
**Related Incident Records:** `INC-0955`, `INC-0710`  
**Discipline:** Utilities Operations & Stationary Equipment Reliability  
**Author:** Rajesh Kumar, Senior Utilities Operations Engineer  
**Approved By:** David Henderson, Boiler House Superintendent  
**Published Date:** August 20, 2026  

---

## 1. Context: Incident INC-0955 (August 14, 2026)

During peak steam generation demand for the East Refinery catalytic cracking unit, Boiler Feed Pump `BFP-101` experienced an abrupt rise in thrust bearing temperature to **91.0°C**, triggering critical alarm `BFP-BEAR-TEMP-HI`. 

Normal bearing temperature is 68°C to 72°C. The emergency trip threshold is **95.0°C**.

### Investigation Findings:
- Cooling water flow rate through plate heat exchanger `HE-101` was nominal at 45 m³/h.
- However, cooling water outlet temperature was only 29.5°C (inlet 27.0°C, delta-T only 2.5°C), while lube oil temperature entering the pump bearings was elevated at 64.0°C.
- Teardown of the plate pack revealed severe calcium carbonate scaling combined with river silt deposits on the cooling water side of plates 12 through 34, reducing effective thermal conductivity by 58%.

---

## 2. Chemical Washing Protocol & Results

To restore heat exchange capacity without an extended pump outage:
1. Operations switched duty to standby Boiler Feed Pump `BFP-102`.
2. A closed-loop chemical cleaning cart was connected to the cooling water inlet and outlet flanges of `HE-101`.
3. Circulated a 5% inhibited citric acid solution heated to 52°C for 3.5 hours with pH monitoring.
4. Scale dissolution was verified as pH stabilized at 3.8.
5. The unit was backflushed with clean condensate water until turbidity dropped below 5 NTU.

### Post-Cleaning Results:
- Upon restarting `BFP-101` at full boiler load, thrust bearing temperature stabilized at **71.5°C** (well below the 80.0°C advisory threshold).
- Heat exchanger cooling water delta-T increased from 2.5°C to 7.8°C.

---

## 3. Recurring Failure Pattern & Preventative Rule (90-Day Policy)

Historical correlation reveals that `BFP-101` experiences high bearing temperature alarms repeatedly over 90-day intervals during high ambient summer months when cooling tower water temperatures peak:
- **Preventative Cleaning Interval:** Chemical descaling of cooler `HE-101` is now scheduled every **75 days** between June and September.
- **Standby Pump Alternation:** Operators must alternate lead pump duty between `BFP-101` and `BFP-102` on the 1st and 15th of every month to ensure even thermal load and verify auxiliary starter reliability.
- **Reference INC-0955:** When logging any high bearing temperature incident on boiler feed pumps, technicians must inspect cooler delta-T before ordering bearing mechanical replacement.
