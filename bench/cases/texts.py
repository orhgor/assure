"""Wordings of the synthetic benchmark documents (frozen with ``bench-v1``).

The auto declarations page, the FNOL, the homeowners declarations and the
CMS-1500 are the labelled golden fixtures of ``tests/golden`` (2026-09-25/26)
copied here verbatim so the benchmark set stays frozen even when the golden
set moves. The other wordings (endorsement, cancellation, renewal, property
claim, repair estimate, adjuster note, coverage schedule) were written for
``bench-v1`` on 2026-09-26 against the plan's corpus taxonomy; their values
are the manifest's expected fields.
"""

from __future__ import annotations

AUTO_DECLARATIONS = """PERSONAL AUTO POLICY DECLARATIONS
Northstar Mutual Automobile Insurance Company

Policy Number: NAP-4471-2025
Named Insured: Daniel R. Whitfield
Mailing Address: 18 Harbor View Lane, Plymouth, MA 02360
Policy Period: 03/01/2025 to 03/01/2026 (12:01 a.m. standard time)

Vehicle: 2003 Honda Accord EX Sedan
VIN: 1HGCM82633A004352
Garaging Location: Plymouth, MA

COVERAGES AND LIMITS
Bodily Injury Liability: $250,000 each person / $500,000 each accident
Property Damage Liability: $100,000 each accident
Collision Deductible: $500
Comprehensive Deductible: $250
Uninsured Motorists: $100,000 / $300,000

Total Premium: $1,486.00 annual

Agent: Marianne Costa, Costa Insurance Agency
Countersigned this 20th day of February, 2025.

Authorized Signature: /s/ Marianne Costa
"""

#: The same declarations page with a blank signature line (the signature
#: cases draw, or do not draw, an ink stroke on the line).
AUTO_DECLARATIONS_BLANK_SIGNATURE = AUTO_DECLARATIONS.replace(
    "Authorized Signature: /s/ Marianne Costa", "Authorized Signature: ______________________________"
)

#: Only the first half of the page survives in the partial-page case; these
#: are the labels above the cut.
AUTO_DECLARATIONS_TOP_HALF_FIELDS = ("policy_number", "insured_name", "effective_date", "expiration_date", "vin", "vehicle_year_make_model")

AUTO_FNOL = """AUTOMOBILE LOSS NOTICE — FIRST NOTICE OF CLAIM
Northstar Mutual Automobile Insurance Company

Claim Number: CLM-2025-093311
Policy Number: NAP-4471-2025
Claimant: Daniel R. Whitfield
Date of Loss: 08/14/2025
Time of Loss: 6:40 p.m.
Location of Accident: Route 3A at Samoset Street, Plymouth, MA

Vehicle: 2003 Honda Accord EX Sedan
VIN: 1HGCM82633A004352

Loss Description: Rear-ended while stopped at a red light; rear bumper, trunk lid and tail lamps damaged. No injuries reported.
Police Report: Plymouth PD #25-11873

Estimated Damage: $4,275.00
Repair Shop: Cordage Park Collision

Adjuster: Thomas Greeley
Claimant's Signature: Daniel R. Whitfield
"""

#: Seeded contradiction: the FNOL names a different VIN than the policy.
AUTO_FNOL_WRONG_VIN = AUTO_FNOL.replace("1HGCM82633A004352", "4T1BF3EK6BU123456").replace(
    "2003 Honda Accord EX Sedan", "2011 Toyota Camry LE"
)

PROPERTY_DECLARATIONS = """HOMEOWNERS POLICY DECLARATIONS — HO-3 SPECIAL FORM
Bay Colony Property & Casualty Insurance Company

Policy Number: HO3-55120-PL
Named Insured: Rosa and Miguel Alvarez
Mailing Address: 42 Sandwich Road, Plymouth, MA 02360
Insured Location: 42 Sandwich Road, Plymouth, MA 02360
Policy Period: 05/01/2025 to 05/01/2026

SECTION I — PROPERTY COVERAGES
Coverage A — Dwelling: $425,000
Coverage B — Other Structures: $42,500
Coverage C — Personal Property: $212,500
Coverage D — Loss of Use: $85,000

SECTION II — LIABILITY COVERAGES
Coverage E — Personal Liability: $300,000 each occurrence
Coverage F — Medical Payments to Others: $5,000 each person

All Peril Deductible: $2,500
Wind/Hail Deductible: 2% of Coverage A

Total Annual Premium: $2,140.00

Mortgagee: Plymouth Savings Bank, ISAOA, PO Box 1620, Plymouth, MA 02362
Agent: Harbor Insurance Associates
Authorized Signature: /s/ Kevin Doyle
"""

CMS_1500 = """HEALTH INSURANCE CLAIM FORM
APPROVED BY NATIONAL UNIFORM CLAIM COMMITTEE (NUCC) 02/12   CMS-1500
PICA   1. MEDICARE [ ]  MEDICAID [ ]  TRICARE [ ]  CHAMPVA [ ]  GROUP HEALTH PLAN [X]  FECA [ ]  OTHER [ ]

1a. Insured's I.D. Number: HGP-2231-0087
2. Patient's Name: Nandakumar, Priya
3. Patient's Birth Date: 09/23/1985   Sex: F
4. Insured's Name: Nandakumar, Priya
5. Patient's Address: 14 Cordage Park Circle, Plymouth, MA 02360
6. Patient Relationship to Insured: Self
10a. Employment? [ ] Yes [X] No   10b. Auto Accident? [ ] Yes [X] No   Place (State): MA   10c. Other Accident? [ ] Yes [X] No
11. Insured's Policy Group or FECA Number: GHP-7781
17. Name of Referring Provider: Dr. Helen Marsh

21. Diagnosis or Nature of Illness or Injury (ICD-10): A. M54.50  B. G89.29  C. Z79.899
24a. Date(s) of Service: 08/14/2025 to 08/14/2025   24b. Place of Service: 11
24d. Procedures, Services, or Supplies (CPT/HCPCS): 99214, 97140, 97110
25. Federal Tax I.D. Number: 04-3456789   SSN [ ]  EIN [X]
26. Patient's Account No.: 55120-A
27. Accept Assignment? [X] Yes [ ] No
28. Total Charge: $412.00
29. Amount Paid: $25.00
30. Rsvd for NUCC Use

31. Signature of Physician or Supplier: /s/ Alan Reyes, MD   Date: 08/15/2025
32. Service Facility Location Information: Plymouth Spine Clinic, 12 Court Street, Plymouth, MA 02360
33. Billing Provider: Plymouth Spine Clinic
    Ph #: (508) 555-0142
33a. Rendering Provider NPI: 1234567893
33b. Federal Tax ID: 04-3456789
"""

AUTO_ENDORSEMENT = """POLICY CHANGE ENDORSEMENT — PERSONAL AUTO
Northstar Mutual Automobile Insurance Company
Endorsement Number: END-04
This endorsement changes the policy. Please read it carefully.

Policy Number: NAP-4471-2025
Named Insured: Daniel R. Whitfield
Endorsement Effective Date: 09/01/2025
Policy Expiration Date: 03/01/2026

CHANGE: Replacement of insured vehicle.
Vehicle removed: 2003 Honda Accord EX Sedan, VIN 1HGCM82633A004352
Vehicle added: 2019 Subaru Outback Premium
VIN: 4S4BSAFC8K3312221

Coverages continue unchanged: Bodily Injury Liability $250,000 / $500,000; Collision Deductible $500; Comprehensive Deductible $250.
Additional Premium for the remainder of the term: $212.00

All other terms and conditions of the policy remain unchanged.
Agent: Marianne Costa, Costa Insurance Agency
Authorized Signature: /s/ Marianne Costa
"""

AUTO_CANCELLATION = """NOTICE OF CANCELLATION — PERSONAL AUTO POLICY
Northstar Mutual Automobile Insurance Company

Policy Number: NAP-4471-2025
Named Insured: Daniel R. Whitfield
Mailing Address: 18 Harbor View Lane, Plymouth, MA 02360
Vehicle: 2003 Honda Accord EX Sedan
VIN: 1HGCM82633A004352

You are hereby notified that the above policy is cancelled, effective 12:01 a.m. standard time on the
Cancellation Effective Date: 11/15/2025
Reason for Cancellation: Non-payment of premium. Amount past due: $248.00.

Unearned Premium to be returned: $0.00
If payment of $248.00 is received before the cancellation effective date, this notice is void and coverage continues.
Reinstatement after the effective date requires a new application and may involve a lapse in coverage.

Agent: Marianne Costa, Costa Insurance Agency
Authorized Signature: /s/ Northstar Mutual Underwriting
"""

#: Seeded contradiction: the renewal offer carries a policy number that does
#: not match the declarations page it renews (a digit transposition).
AUTO_RENEWAL_WRONG_POLICY_NUMBER = """RENEWAL DECLARATIONS — PERSONAL AUTO POLICY
Northstar Mutual Automobile Insurance Company
This renewal replaces your expiring policy for the term shown below.

Policy Number: NAP-4417-2026
Named Insured: Daniel R. Whitfield
Mailing Address: 18 Harbor View Lane, Plymouth, MA 02360
Policy Period: 03/01/2026 to 03/01/2027 (12:01 a.m. standard time)

Vehicle: 2003 Honda Accord EX Sedan
VIN: 1HGCM82633A004352

COVERAGES AND LIMITS
Bodily Injury Liability: $250,000 each person / $500,000 each accident
Collision Deductible: $500
Comprehensive Deductible: $250

Total Premium: $1,532.00 annual

Agent: Marianne Costa, Costa Insurance Agency
Authorized Signature: /s/ Marianne Costa
"""

#: Seeded contradiction: the insured's surname is misspelled on the claim.
AUTO_FNOL_MISSPELLED_INSURED = AUTO_FNOL.replace("Daniel R. Whitfield", "Daniel R. Whitfeld")

PROPERTY_CLAIM = """PROPERTY LOSS NOTICE — HOMEOWNERS
Bay Colony Property & Casualty Insurance Company

Claim Number: PCL-2025-00871
Policy Number: HO3-55120-PL
Claimant: Rosa Alvarez
Date of Loss: 09/02/2025
Property Address: 42 Sandwich Road, Plymouth, MA 02360
Loss Location: Same as property address — kitchen and finished basement

Cause of Loss: Water damage — supply line failure under kitchen sink
Description: Dishwasher supply line burst overnight; standing water in kitchen, seepage into basement ceiling and drywall. Dwelling damage only; no personal property claimed at this time.
Mitigation: ServPro water extraction 09/03/2025

Estimated Damage: $18,640.00
Contractor Estimate attached: Yes

Adjuster: Linda Okonkwo
Claimant's Signature: Rosa Alvarez
"""

REPAIR_ESTIMATE = """REPAIR ESTIMATE
Cordage Park Collision — 10 Cordage Park Circle, Plymouth, MA 02360 — (508) 555-0177

Claim Number: CLM-2025-093311
Policy Number: NAP-4471-2025
Insured: Daniel R. Whitfield
Vehicle: 2003 Honda Accord EX Sedan
VIN: 1HGCM82633A004352
Estimate Date: 08/18/2025
"""

#: Line items for the repair estimate table (part, operation, hours, parts $, labor $).
REPAIR_ESTIMATE_ROWS = (
    ("Rear bumper cover", "Replace", "2.1", "412.00", "168.00"),
    ("Rear bumper reinforcement", "Replace", "1.4", "296.50", "112.00"),
    ("Trunk lid", "Replace", "3.2", "688.00", "256.00"),
    ("Tail lamp assembly LH", "Replace", "0.5", "214.00", "40.00"),
    ("Tail lamp assembly RH", "Replace", "0.5", "214.00", "40.00"),
    ("Rear body panel", "Repair", "4.0", "0.00", "320.00"),
    ("Refinish rear bumper, trunk lid, rear body", "Refinish", "5.6", "186.50", "448.00"),
    ("Wheel alignment check", "Sublet", "0.0", "0.00", "95.00"),
    ("Hazardous waste disposal", "Misc", "0.0", "12.00", "0.00"),
    ("Paint materials", "Misc", "0.0", "116.00", "0.00"),
)
REPAIR_ESTIMATE_TOTAL_LINE = "Estimated Damage (parts + labor + tax): $4,275.00"

ADJUSTER_NOTE = """Adjuster field note
Claim Number: CLM-2025-093311
Date of Loss: 08/14/2025
Inspected vehicle at Cordage Park Collision.
Rear impact, bumper and trunk lid, both tail lamps.
Estimated Damage: $4,275.00
No injuries, police report on file.
Adjuster: Thomas Greeley
"""

#: FNOL form whose printed labels are typed and whose answers are written by
#: hand (the handwritten-fields case): the labels and the values are rendered
#: with different fonts by the generator.
FNOL_FORM_LABELS_AND_VALUES = (
    ("Claim Number:", "CLM-2025-093311"),
    ("Policy Number:", "NAP-4471-2025"),
    ("Claimant:", "Daniel R. Whitfield"),
    ("Date of Loss:", "08/14/2025"),
    ("Vehicle:", "2003 Honda Accord EX Sedan"),
    ("VIN:", "1HGCM82633A004352"),
    ("Estimated Damage:", "$4,275.00"),
    ("Adjuster:", "Thomas Greeley"),
)
FNOL_FORM_TITLE = "AUTOMOBILE LOSS NOTICE — FIRST NOTICE OF CLAIM\nNorthstar Mutual Automobile Insurance Company"

COVERAGE_SCHEDULE_HEADER = """SCHEDULE OF COVERAGES AND FORMS — HOMEOWNERS HO-3
Bay Colony Property & Casualty Insurance Company
Policy Number: HO3-55120-PL
Named Insured: Rosa and Miguel Alvarez
Insured Location: 42 Sandwich Road, Plymouth, MA 02360
Policy Period: 05/01/2025 to 05/01/2026
"""
#: Coverage table rows (coverage, form, limit, deductible, premium).
COVERAGE_SCHEDULE_ROWS = (
    ("Coverage A — Dwelling", "HO 00 03", "$425,000", "$2,500", "$1,412.00"),
    ("Coverage B — Other Structures", "HO 00 03", "$42,500", "$2,500", "$96.00"),
    ("Coverage C — Personal Property", "HO 00 03", "$212,500", "$2,500", "$318.00"),
    ("Coverage D — Loss of Use", "HO 00 03", "$85,000", "—", "$44.00"),
    ("Coverage E — Personal Liability", "HO 24 82", "$300,000", "—", "$180.00"),
    ("Coverage F — Medical Payments", "HO 24 82", "$5,000", "—", "$18.00"),
    ("Water Backup", "HO 04 95", "$10,000", "$1,000", "$48.00"),
    ("Scheduled Jewelry", "HO 04 61", "$12,000", "$0", "$24.00"),
)
COVERAGE_SCHEDULE_FOOTER = """Total Annual Premium: $2,140.00
Forms and endorsements made part of this policy: HO 00 03 05 11, HO 04 61 05 11, HO 04 95 05 11, HO 24 82 05 11, MA AMEND 01 22.
Agent: Harbor Insurance Associates
"""
