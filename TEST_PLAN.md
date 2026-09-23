# Assure Test Plan - 10 ICP Personas

## Overview
This test plan defines 10 different test personas from different Ideal Customer Profiles (ICPs). Each persona performs a sequence of 37-42 actions to test the system from start to finish.

**Staging URL:** `https://staging.getassureai.com`

---

## Persona 1: Insurance Claims Adjuster (ICP: Insurance)

Tests: Document verification for insurance claims, policy review, claim investigation

**Actions (40 actions):**
1. Navigate to `https://staging.getassureai.com`
2. Verify home page loads (title, branding)
3. Click "Sign in"
4. Verify sign-in page loads (auth options)
5. Enter access key if required (or verify self-hosted mode)
6. Verify access to dashboard
7. Create new project: "Claim-E2E-001"
8. Verify project creation (201)
9. Navigate to project
10. Click "Upload document"
11. Select insurance policy PDF (150 pages)
12. Verify upload starts
13. Wait for upload completion
14. Verify upload success (200)
15. Verify document appears in project
16. Click on uploaded document
17. Verify document details (filename, page count, parser)
18. Click "Parse" or verify auto-parse
19. Wait for parsing completion
20. Verify parsing results (confidence scores, chunks)
21. Verify OMP artifact created
22. Navigate to "Verification"
23. Click "Verify claims"
24. Verify verification starts
25. Wait for verification completion
26. Verify verification results (anchored, supported, unanchored claims)
27. Click on an unanchored claim
28. Verify claim details (source quote, page number)
29. Click "Red-Hat review"
30. Verify Red-Hat starts
31. Wait for Red-Hat completion
32. Verify Red-Hat findings
33. Navigate to "Export"
34. Click "Export as PDF"
35. Verify export starts
36. Wait for export completion
37. Download exported PDF
38. Verify exported PDF contains all claims with verification states
39. Verify export includes source manifest
40. Log out, verify redirect to sign-in

---

## Persona 2: Legal Document Reviewer (ICP: Legal)

Tests: Contract review, clause verification, legal document parsing

**Actions (37 actions):**
1. Navigate to staging
2. Sign in
3. Create project: "Contract-Review-E2E-002"
4. Upload contract PDF (50 pages, complex clauses)
5. Verify upload success
6. Wait for parsing
7. Verify parsing results (clause extraction, confidence)
8. Navigate to "Compile"
9. Enter intent: "Review all liability clauses"
10. Click "Compile"
11. Verify compilation starts
12. Wait for compilation
13. Verify compiled document (liability clauses extracted)
14. Click on each compiled claim
15. Verify each claim has source anchor
16. Verify each claim has verification state
17. Navigate to "Red-Hat"
18. Click "Run Red-Hat"
19. Verify Red-Hat starts
20. Wait for Red-Hat
21. Verify Red-Hat findings (unsupported claims flagged)
22. Click on a Red-Hat finding
23. Verify finding details
24. Click "Apply suggestion" on a finding
25. Verify suggestion applied
26. Navigate to "Export"
27. Select "Export with verification"
28. Click "Export"
29. Verify export starts
30. Download exported document
31. Verify exported document has all claims with verification
32. Verify exported document has source manifest
33. Create second project: "Contract-Compare-E2E-003"
34. Upload second contract PDF
35. Verify upload and parse
36. Use "Compare" feature to compare two contracts
37. Verify comparison results (differences highlighted)

---

## Persona 3: Financial Auditor (ICP: Finance)

Tests: Financial statement verification, number auditing, compliance checking

**Actions (42 actions):**
1. Navigate to staging
2. Sign in
3. Create project: "Audit-Fin-E2E-004"
4. Upload financial statement PDF (100 pages, tables, figures)
5. Verify upload
6. Wait for parsing
7. Verify parsing (table extraction, figure extraction)
8. Verify parse confidence scores
9. Navigate to "Compile"
10. Enter intent: "Verify all financial figures against source"
11. Click "Compile"
12. Verify compilation
13. Wait for compilation
14. Verify compiled claims (each figure is a claim)
15. Verify each claim has source anchor (page, table)
16. Verify each claim has numeral audit status
17. Navigate to "Verification"
18. Verify all claims have verification states
19. Click on claims with "unsupported" status
20. Verify unsupported claims have source quote
21. Navigate to "Red-Hat"
22. Click "Run Red-Hat"
23. Verify Red-Hat starts
24. Wait for Red-Hat
25. Verify Red-Hat findings (claims that source only implies)
26. Click on Red-Hat finding
27. Verify finding details (source quote)
28. Click "Apply fix"
29. Verify fix applied
30. Navigate to "Export"
31. Export with full verification
32. Download export
33. Verify export has all claims with verification
34. Verify export has numeral audit results
35. Verify export has Red-Hat findings
36. Create report summary
37. Verify summary includes all verification stats
38. Verify summary includes Red-Hat stats
39. Verify summary includes numeral audit stats
40. Export summary as PDF
41. Download summary PDF
42. Verify summary PDF is complete

---

## Persona 4: Healthcare Document Reviewer (ICP: Healthcare)

Tests: Medical document review, treatment verification, compliance checking

**Actions (38 actions):**
1. Navigate to staging
2. Sign in
3. Create project: "Medical-Review-E2E-005"
4. Upload medical report PDF (80 pages, treatment plans)
5. Verify upload
6. Wait for parsing
7. Verify parsing (treatment extraction, medical terms)
8. Verify confidence scores
9. Navigate to "Compile"
10. Enter intent: "Verify all treatment recommendations"
11. Click "Compile"
12. Verify compilation
13. Wait for compilation
14. Verify compiled claims (each treatment is a claim)
15. Verify each claim has source anchor
16. Verify each claim has verification state
17. Navigate to "Verification"
18. Verify all claims have verification
19. Identify claims with "partial" support
20. Click on partial support claim
21. Verify partial claim details
22. Navigate to "Red-Hat"
23. Click "Run Red-Hat"
24. Verify Red-Hat starts
25. Wait for Red-Hat
26. Verify Red-Hat findings
27. Click on Red-Hat finding
28. Verify finding details
29. Navigate to "Export"
30. Export with verification
31. Download export
32. Verify export has all claims with verification
33. Verify export has source manifest
34. Create second project: "Treatment-Compare-E2E-006"
35. Upload second medical report
36. Verify upload and parse
37. Compare two treatment plans
38. Verify comparison results

---

## Persona 5: Government Contract Reviewer (ICP: Government)

Tests: Contract compliance, regulation verification, clause checking

**Actions (40 actions):**
1. Navigate to staging
2. Sign in
3. Create project: "Contract-Gov-E2E-007"
4. Upload government contract PDF (200 pages, regulations)
5. Verify upload
6. Wait for parsing
7. Verify parsing (regulation extraction, clause identification)
8. Verify confidence scores
9. Navigate to "Compile"
10. Enter intent: "Verify all compliance clauses"
11. Click "Compile"
12. Verify compilation
13. Wait for compilation
14. Verify compiled claims (each compliance clause is a claim)
15. Verify each claim has source anchor
16. Verify each claim has verification state
17. Navigate to "Verification"
18. Verify all claims have verification
19. Identify claims with "unsupported" status
20. Click on unsupported claim
21. Verify unsupported claim details
22. Navigate to "Red-Hat"
23. Click "Run Red-Hat"
24. Verify Red-Hat starts
25. Wait for Red-Hat
26. Verify Red-Hat findings
27. Click on Red-Hat finding
28. Verify finding details
29. Click "Apply suggestion"
30. Verify suggestion applied
31. Navigate to "Export"
32. Export with full verification
33. Download export
34. Verify export has all claims with verification
35. Verify export has source manifest
36. Create compliance report
37. Verify report includes all verification stats
38. Verify report includes Red-Hat stats
39. Export report as PDF
40. Download report PDF

---

## Persona 6: Academic Researcher (ICP: Academic)

Tests: Research paper verification, citation checking, claim verification

**Actions (37 actions):**
1. Navigate to staging
2. Sign in
3. Create project: "Research-Paper-E2E-008"
4. Upload research paper PDF (60 pages, citations, claims)
5. Verify upload
6. Wait for parsing
7. Verify parsing (citation extraction, claim identification)
8. Verify confidence scores
9. Navigate to "Compile"
10. Enter intent: "Verify all research claims"
11. Click "Compile"
12. Verify compilation
13. Wait for compilation
14. Verify compiled claims (each research claim is a claim)
15. Verify each claim has source anchor (citation)
16. Verify each claim has verification state
17. Navigate to "Verification"
18. Verify all claims have verification
19. Identify claims with "unanchored" status
20. Click on unanchored claim
21. Verify unanchored claim details
22. Navigate to "Red-Hat"
23. Click "Run Red-Hat"
24. Verify Red-Hat starts
25. Wait for Red-Hat
26. Verify Red-Hat findings
27. Click on Red-Hat finding
28. Verify finding details
29. Navigate to "Export"
30. Export with verification
31. Download export
32. Verify export has all claims with verification
33. Verify export has citation manifest
34. Create research summary
35. Verify summary includes all verification stats
36. Export summary as PDF
37. Verify summary PDF is complete

---

## Persona 7: Corporate Compliance Officer (ICP: Corporate)

Tests: Compliance document verification, policy checking, regulation review

**Actions (40 actions):**
1. Navigate to staging
2. Sign in
3. Create project: "Compliance-Corp-E2E-009"
4. Upload compliance policy PDF (120 pages, regulations)
5. Verify upload
6. Wait for parsing
7. Verify parsing (policy extraction, regulation identification)
8. Verify confidence scores
9. Navigate to "Compile"
10. Enter intent: "Verify all compliance requirements"
11. Click "Compile"
12. Verify compilation
13. Wait for compilation
14. Verify compiled claims (each compliance requirement is a claim)
15. Verify each claim has source anchor
16. Verify each claim has verification state
17. Navigate to "Verification"
18. Verify all claims have verification
19. Identify claims with "unsupported" status
20. Click on unsupported claim
21. Verify unsupported claim details
22. Navigate to "Red-Hat"
23. Click "Run Red-Hat"
24. Verify Red-Hat starts
25. Wait for Red-Hat
26. Verify Red-Hat findings
27. Click on Red-Hat finding
28. Verify finding details
29. Click "Apply fix"
30. Verify fix applied
31. Navigate to "Export"
32. Export with full verification
33. Download export
34. Verify export has all claims with verification
35. Verify export has source manifest
36. Create compliance summary
37. Verify summary includes all verification stats
38. Verify summary includes Red-Hat stats
39. Export summary as PDF
40. Download summary PDF

---

## Persona 8: Real Estate Professional (ICP: Real Estate)

Tests: Property document verification, title review, contract checking

**Actions (37 actions):**
1. Navigate to staging
2. Sign in
3. Create project: "Real-Estate-E2E-010"
4. Upload property document PDF (100 pages, title, contracts)
5. Verify upload
6. Wait for parsing
7. Verify parsing (property extraction, title identification)
8. Verify confidence scores
9. Navigate to "Compile"
10. Enter intent: "Verify all property claims"
11. Click "Compile"
12. Verify compilation
13. Wait for compilation
14. Verify compiled claims (each property claim is a claim)
15. Verify each claim has source anchor
16. Verify each claim has verification state
17. Navigate to "Verification"
18. Verify all claims have verification
19. Identify claims with "partial" support
20. Click on partial support claim
21. Verify partial claim details
22. Navigate to "Red-Hat"
23. Click "Run Red-Hat"
24. Verify Red-Hat starts
25. Wait for Red-Hat
26. Verify Red-Hat findings
27. Click on Red-Hat finding
28. Verify finding details
29. Navigate to "Export"
30. Export with verification
31. Download export
32. Verify export has all claims with verification
33. Verify export has source manifest
34. Create second project: "Title-Compare-E2E-011"
35. Upload second title document
36. Verify upload and parse
37. Compare two title documents

---

## Persona 9: Consulting Firm Analyst (ICP: Consulting)

Tests: Consulting report verification, recommendation checking, analysis verification

**Actions (42 actions):**
1. Navigate to staging
2. Sign in
3. Create project: "Consulting-Report-E2E-012"
4. Upload consulting report PDF (150 pages, recommendations)
5. Verify upload
6. Wait for parsing
7. Verify parsing (recommendation extraction, analysis identification)
8. Verify confidence scores
9. Navigate to "Compile"
10. Enter intent: "Verify all consulting recommendations"
11. Click "Compile"
12. Verify compilation
13. Wait for compilation
14. Verify compiled claims (each recommendation is a claim)
15. Verify each claim has source anchor
16. Verify each claim has verification state
17. Navigate to "Verification"
18. Verify all claims have verification
19. Identify claims with "unsupported" status
20. Click on unsupported claim
21. Verify unsupported claim details
22. Navigate to "Red-Hat"
23. Click "Run Red-Hat"
24. Verify Red-Hat starts
25. Wait for Red-Hat
26. Verify Red-Hat findings
27. Click on Red-Hat finding
28. Verify finding details
29. Click "Apply suggestion"
30. Verify suggestion applied
31. Navigate to "Export"
32. Export with full verification
33. Download export
34. Verify export has all claims with verification
35. Verify export has source manifest
36. Create consulting summary
37. Verify summary includes all verification stats
38. Verify summary includes Red-Hat stats
39. Export summary as PDF
40. Download summary PDF
41. Create second project: "Client-Compare-E2E-013"
42. Upload second consulting report and compare

---

## Persona 10: Freelance Document Reviewer (ICP: Freelance)

Tests: Freelance document review, multi-client document verification, quality checking

**Actions (40 actions):**
1. Navigate to staging
2. Sign in
3. Create project: "Freelance-Review-E2E-014"
4. Upload client document PDF (80 pages, various documents)
5. Verify upload
6. Wait for parsing
7. Verify parsing (document extraction, content identification)
8. Verify confidence scores
9. Navigate to "Compile"
10. Enter intent: "Verify all document claims"
11. Click "Compile"
12. Verify compilation
13. Wait for compilation
14. Verify compiled claims (each document claim is a claim)
15. Verify each claim has source anchor
16. Verify each claim has verification state
17. Navigate to "Verification"
18. Verify all claims have verification
19. Identify claims with "unanchored" status
20. Click on unanchored claim
21. Verify unanchored claim details
22. Navigate to "Red-Hat"
23. Click "Run Red-Hat"
24. Verify Red-Hat starts
25. Wait for Red-Hat
26. Verify Red-Hat findings
27. Click on Red-Hat finding
28. Verify finding details
29. Navigate to "Export"
30. Export with verification
31. Download export
32. Verify export has all claims with verification
33. Verify export has source manifest
34. Create quality report
35. Verify report includes all verification stats
36. Verify report includes Red-Hat stats
37. Export report as PDF
38. Download report PDF
39. Create second project: "Second-Client-E2E-015"
40. Repeat process for second client

---

## Common Test Scenarios (all personas)

### Authentication Tests
- [ ] Sign in with access key (if required)
- [ ] Verify sign-in page loads
- [ ] Verify sign-up page loads
- [ ] Verify sign-out works
- [ ] Verify session persists

### Upload Tests
- [ ] Upload valid PDF (various sizes: 10 pages, 50 pages, 100 pages, 200 pages)
- [ ] Verify upload success
- [ ] Verify upload fails for non-PDF (if restricted)
- [ ] Verify upload fails for oversized file (if limit exists)

### Parsing Tests
- [ ] Verify parsing starts automatically after upload
- [ ] Verify parsing results (filename, page count, parser name, confidence)
- [ ] Verify parsing handles different PDF types (born-digital, scanned)
- [ ] Verify parsing handles different content types (text, tables, figures)

### Compile Tests
- [ ] Enter various intents (different questions, different verification goals)
- [ ] Verify compilation starts
- [ ] Verify compilation completes
- [ ] Verify compiled claims have source anchors
- [ ] Verify compiled claims have verification states

### Verification Tests
- [ ] Verify verification starts automatically after compilation
- [ ] Verify verification results (anchored, supported, partial, unanchored)
- [ ] Click on claims with different verification states
- [ ] Verify claim details (source quote, page number, confidence)

### Red-Hat Tests
- [ ] Click "Run Red-Hat" on a document
- [ ] Verify Red-Hat starts
- [ ] Verify Red-Hat completion
- [ ] Verify Red-Hat findings (claims that source only implies)
- [ ] Click on Red-Hat findings
- [ ] Verify finding details (source quote, paragraph reference)
- [ ] Click "Apply suggestion" on findings
- [ ] Verify suggestion applied

### Export Tests
- [ ] Click "Export" on a document
- [ ] Verify export starts
- [ ] Verify export completes
- [ ] Download exported document
- [ ] Verify exported document has all claims with verification
- [ ] Verify exported document has source manifest
- [ ] Export with different formats (PDF, if available)

### Project Management Tests
- [ ] Create multiple projects
- [ ] Switch between projects
- [ ] Verify project switching works
- [ ] Delete a project (if supported)
- [ ] Verify project deletion (if supported)

### Error Handling Tests
- [ ] Upload corrupt PDF
- [ ] Verify error response (not 500, but controlled error)
- [ ] Upload non-PDF file (if restricted)
- [ ] Verify error response
- [ ] Upload oversized file (if limit exists)
- [ ] Verify error response

### Edge Cases
- [ ] Upload empty PDF (if possible)
- [ ] Verify error response
- [ ] Upload PDF with only images (no text)
- [ ] Verify parsing handles it (scanned PDF)
- [ ] Upload PDF with mixed content (text + images)
- [ ] Verify parsing handles it

---

## Functionality Checklist (no functionality issues)

Each persona should verify these functionality points:

### Upload
- [ ] PDF upload works
- [ ] Upload progress shown
- [ ] Upload completion confirmed
- [ ] Document appears in project

### Parsing
- [ ] Parsing starts automatically
- [ ] Parsing progress shown (if available)
- [ ] Parsing completion confirmed
- [ ] Document details shown (filename, page count, parser)
- [ ] Confidence scores shown
- [ ] Chunks shown (if available)
- [ ] OMP artifact created

### Compile
- [ ] Compile input accepts intent
- [ ] Compile starts
- [ ] Compile progress shown (if available)
- [ ] Compile completion confirmed
- [ ] Compiled claims shown
- [ ] Each claim has source anchor
- [ ] Each claim has verification state

### Verification
- [ ] Verification starts automatically
- [ ] Verification progress shown (if available)
- [ ] Verification completion confirmed
- [ ] Claims have verification states
- [ ] Claims can be clicked
- [ ] Claim details shown (source quote, page, confidence)

### Red-Hat
- [ ] Red-Hat can be started
- [ ] Red-Hat progress shown (if available)
- [ ] Red-Hat completion confirmed
- [ ] Red-Hat findings shown
- [ ] Findings can be clicked
- [ ] Finding details shown (source quote, paragraph)
- [ ] Suggestions can be applied
- [ ] Suggestions applied confirmed

### Export
- [ ] Export can be started
- [ ] Export progress shown (if available)
- [ ] Export completion confirmed
- [ ] Export downloaded
- [ ] Exported document has claims with verification
- [ ] Exported document has source manifest

### Navigation
- [ ] Can navigate between pages
- [ ] Can navigate between projects
- [ ] Navigation is intuitive

### Error Handling
- [ ] Errors are shown clearly
- [ ] Errors are not generic 500
- [ ] Errors are actionable
