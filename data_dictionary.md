# Insurance Claims Processing — Data Dictionary (Advanced v2.0)

## Overview

This document defines all **14 tables** in the advanced insurance claims data model:
- **11 Core Domain Entities**: `Customer`, `Adjuster`, `Policy`, `Coverage`, `Incident`, `Claim`, `ClaimItem`, `Document`, `Assessment`, `Reserve`, `Settlement`
- **2 Normalized Many-to-Many (N:M) Bridge Tables**: `ClaimAdjusterAssignment` (`claim_adjuster_assignments`) and `ClaimCoverageAllocation` (`claim_coverage_allocations`)
- **1 Immutable Event History Table**: `ClaimStateHistory` (`claim_state_history`)

### Data Type and Unit Conventions

- **Monetary Fields**: All fields ending in `_amount`, `_limit`, `_premium`, `_deductible`, `_reserve`, or `_cost` are decimal **USD** amounts rounded to two decimal places (`0.01`).
- **Percentage Fields**: `co_pay_percentage` and `liability_percentage` are integer percentage points (`0`–`100`).
- **Scores & Weights**: `fraud_score` is a decimal probability in `[0.000, 1.000]`; `workload_weight` is a decimal capacity weight in `(0.0, 1.0]`.
- **Sizes & Counts**: `file_size_kb` is measured in kilobytes (KB); `term_months` in calendar months; `quantity`, `witnesses`, and `max_concurrent_claims` are non-negative integer counts.
- **Temporal Fields**: `DATE` fields use ISO-8601 `YYYY-MM-DD`; `TIMESTAMP` fields use `YYYY-MM-DD HH:MM:SS` (or UTC ISO-8601 in live API events).

## Entity-Relationship & Cardinality Summary

| Relationship | Cardinality | Bridge / FK Mechanism | Notes |
|---|---|---|---|
| Customer → Policy | 1:N | `policies.customer_id` | One customer holds one or more policies |
| Policy → Coverage | 1:N | `coverages.policy_id` | Each policy defines 2–4 coverage lines |
| Policy → Claim | 1:N | `claims.policy_id` | A policy may have multiple claims over time |
| Incident → Claim | 1:N | `claims.incident_id` | An incident triggers one or more claims |
| Claim ↔ Adjuster | **N:M** | `claim_adjuster_assignments` | Primary adjuster + optional SIU investigator / supervisor reviewer |
| Claim ↔ Coverage | **N:M** | `claim_coverage_allocations` | Primary coverage + optional secondary coverage split for multi-line claims |
| Claim → ClaimItem | 1:N | `claim_items.claim_id` | 1–4 itemized loss lines; `SUM(claimed_amount) == claims.claim_amount` |
| Claim → Document | 1:N | `documents.claim_id` | 3–4 mandatory product-specific evidence records |
| Claim → Assessment | 1:1 (optional) | `assessments.claim_id` (UNIQUE) | Present for all reviewed/assessed claims (`status != SUBMITTED`) |
| Claim → Reserve | 1:1 (optional) | `reserves.claim_id` (UNIQUE) | Present for all active/settled claims (`status NOT IN (SUBMITTED, DENIED)`) |
| Claim → Settlement | 1:N (optional) | `settlements.claim_id` | 1:1 for normal settled claims; 1:N for `CLAIM_PAID_TWICE` anomaly cases |
| Claim → ClaimStateHistory | 1:N | `claim_state_history.claim_id` | Append-only lifecycle transition audit log |

## 1. Customer (`customers`)

| Field | Type | Required | Unique | Units | Allowed Values / Validation Rule |
|---|---|---|---|---|---|
| `customer_id` | VARCHAR(15) | Required (PK) | Yes | ID | `CUST-NNNNNN` (zero-padded 6-digit counter) |
| `first_name` | VARCHAR(50) | Required | No | Text | Non-empty alphabetic string |
| `last_name` | VARCHAR(50) | Required | No | Text | Non-empty alphabetic string |
| `date_of_birth` | DATE | Required | No | Date | `YYYY-MM-DD`, adult policyholder (`1950-01-01` to `2002-12-31`) |
| `email` | VARCHAR(100) | Required | No | Email | Valid `user@domain` format |
| `phone` | VARCHAR(20) | Required | No | Phone | `(NNN) NNN-NNNN` |
| `address_line1` | VARCHAR(200) | Required | No | Text | Street number and street name |
| `city` | VARCHAR(50) | Required | No | Text | Valid city corresponding to `state` |
| `state` | CHAR(2) | Required | No | Code | 2-letter US state postal code |
| `zip_code` | CHAR(5) | Required | No | Code | 5-digit numeric postal code |
| `risk_tier` | VARCHAR(12) | Required | No | Enum | `LOW`, `STANDARD`, `ELEVATED` |
| `created_at` | TIMESTAMP | Required | No | UTC | `YYYY-MM-DD HH:MM:SS` |
| `status` | VARCHAR(10) | Required | No | Enum | `ACTIVE`, `INACTIVE` |

## 2. Adjuster (`adjusters`)

| Field | Type | Required | Unique | Units | Allowed Values / Validation Rule |
|---|---|---|---|---|---|
| `adjuster_id` | VARCHAR(15) | Required (PK) | Yes | ID | `ADJ-NNNNNN` |
| `first_name` | VARCHAR(50) | Required | No | Text | Non-empty name |
| `last_name` | VARCHAR(50) | Required | No | Text | Non-empty name |
| `email` | VARCHAR(100) | Required | No | Email | Valid email |
| `phone` | VARCHAR(20) | Required | No | Phone | `(NNN) NNN-NNNN` |
| `specializations` | TEXT | Required | No | List | Comma-separated subset of `AUTO,HOME,HEALTH,LIFE,TRAVEL` |
| `role_tier` | VARCHAR(20) | Required | No | Enum | `SENIOR_ADJUSTER`, `STANDARD_ADJUSTER`, `SIU_SPECIALIST` |
| `license_number` | VARCHAR(20) | Required | Yes | ID | `ADJ-LIC-NNNNNN` |
| `hire_date` | DATE | Required | No | Date | `YYYY-MM-DD` |
| `status` | VARCHAR(10) | Required | No | Enum | `ACTIVE`, `ON_LEAVE`, `INACTIVE` |
| `max_concurrent_claims` | INT | Required | No | Count | `15`, `20`, `25`, `30` (`> 0`) |

## 3. Policy (`policies`)

| Field | Type | Required | Unique | Units | Allowed Values / Validation Rule |
|---|---|---|---|---|---|
| `policy_id` | VARCHAR(30) | Required (PK) | Yes | ID | `POL-<PRODUCT>-2026-NNNNNN` |
| `customer_id` | VARCHAR(15) | Required (FK) | No | ID | Must exist in `customers.customer_id` |
| `product_type` | VARCHAR(10) | Required | No | Enum | `AUTO`, `HOME`, `HEALTH`, `LIFE`, `TRAVEL` |
| `policy_number` | VARCHAR(30) | Required | Yes | ID | Matches `policy_id` |
| `effective_date` | DATE | Required | No | Date | `< expiration_date` |
| `expiration_date` | DATE | Required | No | Date | `> effective_date` |
| `term_months` | INT | Required | No | Months | `12`, `24`, `36` |
| `premium_amount` | DECIMAL(12,2) | Required | No | USD | `> 0.00` |
| `payment_frequency` | VARCHAR(15) | Required | No | Enum | `MONTHLY`, `QUARTERLY`, `SEMI_ANNUAL`, `ANNUAL` |
| `status` | VARCHAR(12) | Required | No | Enum | `ACTIVE`, `EXPIRED`, `CANCELLED`, `LAPSED` |
| `cancellation_date` | DATE | Optional | No | Date | Non-null when `status == 'CANCELLED'` |
| `lapse_date` | DATE | Optional | No | Date | Non-null when `status == 'LAPSED'` |
| `underwriting_status` | VARCHAR(15) | Required | No | Enum | `APPROVED`, `CONDITIONAL`, `DECLINED` |
| `created_at` | TIMESTAMP | Required | No | UTC | Policy creation timestamp |
| `updated_at` | TIMESTAMP | Required | No | UTC | `>= created_at` |

## 4. Coverage (`coverages`)

| Field | Type | Required | Unique | Units | Allowed Values / Validation Rule |
|---|---|---|---|---|---|
| `coverage_id` | VARCHAR(40) | Required (PK) | Yes | ID | `COV-<policy_id>-NNN` |
| `policy_id` | VARCHAR(30) | Required (FK) | No | ID | Must exist in `policies.policy_id` |
| `coverage_type` | VARCHAR(30) | Required | No | Enum | Product-specific coverage line |
| `coverage_limit` | DECIMAL(12,2) | Required | No | USD | `> 0.00` |
| `deductible` | DECIMAL(12,2) | Required | No | USD | `>= 0.00` (`0.00` for `LIFE`) |
| `co_pay_percentage` | INT | Required | No | % | `0`, `10`, `20` (`0` for non-`HEALTH`) |
| `exclusions` | TEXT | Optional | No | List | Comma-separated excluded peril codes |
| `effective_date` | DATE | Required | No | Date | Must equal parent `policies.effective_date` |
| `expiration_date` | DATE | Required | No | Date | Must equal parent `policies.expiration_date` |
| `status` | VARCHAR(10) | Required | No | Enum | `ACTIVE`, `INACTIVE` |

## 5. Incident (`incidents`)

| Field | Type | Required | Unique | Units | Allowed Values / Validation Rule |
|---|---|---|---|---|---|
| `incident_id` | VARCHAR(20) | Required (PK) | Yes | ID | `INC-2026-NNNNNN` |
| `incident_type` | VARCHAR(35) | Required | No | Enum | Peril type valid for `product_category` |
| `incident_date` | DATE | Required | No | Date | Date of loss occurrence |
| `incident_time` | TIME | Required | No | Time | `HH:MM:SS` |
| `description` | TEXT | Required | No | Text | Narrative summary |
| `location_city` | VARCHAR(50) | Required | No | Text | City of occurrence |
| `location_state` | CHAR(2) | Required | No | Code | 2-letter US state |
| `severity` | VARCHAR(10) | Required | No | Enum | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |
| `police_report_filed` | BOOLEAN | Required | No | Flag | `True` / `False` |
| `witnesses` | INT | Required | No | Count | `0`–`4` |
| `product_category` | VARCHAR(10) | Required | No | Enum | `AUTO`, `HOME`, `HEALTH`, `LIFE`, `TRAVEL` |
| `reported_date` | DATE | Required | No | Date | `>= incident_date` |

## 6. Claim (`claims`)

| Field | Type | Required | Unique | Units | Allowed Values / Validation Rule |
|---|---|---|---|---|---|
| `claim_id` | VARCHAR(20) | Required (PK) | Yes | ID | `CLM-2026-NNNNNN` |
| `policy_id` | VARCHAR(30) | Required (FK) | No | ID | Must exist in `policies.policy_id` |
| `customer_id` | VARCHAR(15) | Required (FK) | No | ID | Must match `policies.customer_id` |
| `incident_id` | VARCHAR(20) | Required (FK) | No | ID | Must exist in `incidents.incident_id` |
| `claim_type` | VARCHAR(10) | Required | No | Enum | `AUTO`, `HOME`, `HEALTH`, `LIFE`, `TRAVEL` (matches policy & incident) |
| `claim_amount` | DECIMAL(12,2) | Required | No | USD | Strictly derived: `== ROUND(SUM(claim_items.claimed_amount), 2)` |
| `submission_date` | DATE | Required | No | Date | `>= incidents.reported_date` |
| `status` | VARCHAR(20) | Required | No | Enum | `SUBMITTED`, `PENDING_DOCUMENTS`, `UNDER_REVIEW`, `SIU_INVESTIGATION`, `APPROVED`, `DENIED`, `SETTLED`, `CLOSED` |
| `assigned_adjuster_id` | VARCHAR(15) | Required (FK) | No | ID | Active adjuster specialized in `claim_type` |
| `priority` | VARCHAR(10) | Required | No | Enum | `LOW`, `MEDIUM`, `HIGH`, `URGENT` |
| `denial_reason` | VARCHAR(50) | Optional | No | Code | Required when `status == 'DENIED'`, null otherwise |
| `fraud_indicator` | BOOLEAN | Required | No | Flag | `True` routes claim to `SIU_INVESTIGATION` or `DENIED` |
| `coverage_id` | VARCHAR(40) | Required (FK) | No | ID | Primary coverage in `coverages.coverage_id` |
| `created_at` | TIMESTAMP | Required | No | UTC | Intake timestamp |
| `updated_at` | TIMESTAMP | Required | No | UTC | `>= created_at` |

## 7. ClaimItem (`claim_items`)

| Field | Type | Required | Unique | Units | Allowed Values / Validation Rule |
|---|---|---|---|---|---|
| `claim_item_id` | VARCHAR(30) | Required (PK) | Yes | ID | `CLI-<claim_id>-NNN` |
| `claim_id` | VARCHAR(20) | Required (FK) | No | ID | Must exist in `claims.claim_id` |
| `item_description` | TEXT | Required | No | Text | Line-item loss description |
| `item_category` | VARCHAR(10) | Required | No | Enum | Matches parent `claims.claim_type` |
| `claimed_amount` | DECIMAL(12,2) | Required | No | USD | Strictly derived: `== ROUND(quantity * unit_cost, 2)` |
| `approved_amount` | DECIMAL(12,2) | Optional | No | USD | `<= claimed_amount` when approved; `0.00` when denied; null when pending |
| `quantity` | INT | Required | No | Count | `1`–`3` |
| `unit_cost` | DECIMAL(12,2) | Required | No | USD | `> 0.00` |
| `status` | VARCHAR(10) | Required | No | Enum | `PENDING`, `APPROVED`, `DENIED` |

## 8. Document (`documents`)

| Field | Type | Required | Unique | Units | Allowed Values / Validation Rule |
|---|---|---|---|---|---|
| `document_id` | VARCHAR(30) | Required (PK) | Yes | ID | `DOC-<claim_id>-NNN` |
| `claim_id` | VARCHAR(20) | Required (FK) | No | ID | Must exist in `claims.claim_id` |
| `document_type` | VARCHAR(30) | Required | No | Enum | Product-specific mandatory document code |
| `file_name` | VARCHAR(100) | Required | No | Text | Standardized `<type>_<claim_id>.pdf` |
| `file_size_kb` | INT | Required | No | KB | `80`–`4800` |
| `upload_date` | DATE | Required | No | Date | `>= claims.submission_date` |
| `uploaded_by` | VARCHAR(15) | Required (FK) | No | ID | Matches `claims.customer_id` |
| `status` | VARCHAR(10) | Required | No | Enum | `REQUESTED`, `RECEIVED`, `VERIFIED`, `REJECTED` |
| `verification_date` | DATE | Optional | No | Date | Non-null when `status == 'VERIFIED'` |
| `notes` | TEXT | Optional | No | Text | Audit note |

## 9. Assessment (`assessments`)

| Field | Type | Required | Unique | Units | Allowed Values / Validation Rule |
|---|---|---|---|---|---|
| `assessment_id` | VARCHAR(30) | Required (PK) | Yes | ID | `ASN-<claim_id>-NNN` |
| `claim_id` | VARCHAR(20) | Required (FK) | Yes | ID | 1:1 with reviewed `claims.claim_id` |
| `adjuster_id` | VARCHAR(15) | Required (FK) | No | ID | Matches `claims.assigned_adjuster_id` |
| `assessment_date` | DATE | Required | No | Date | `> claims.submission_date` |
| `assessed_amount` | DECIMAL(12,2) | Required | No | USD | Strictly `== ROUND(SUM(claim_items.approved_amount), 2)` for approved claims; `0.00` when denied |
| `deductible_applied` | DECIMAL(12,2) | Required | No | USD | Matches `coverages.deductible` |
| `co_pay_percentage` | INT | Required | No | % | Matches `coverages.co_pay_percentage` |
| `eligible_amount` | DECIMAL(12,2) | Required | No | USD | `0.00` if denied; else `MIN(MAX(0, assessed_amount - deductible_applied), coverage_limit)` |
| `coverage_limit` | DECIMAL(12,2) | Required | No | USD | Matches `coverages.coverage_limit` |
| `coverage_verified` | BOOLEAN | Required | No | Flag | `True` for eligible claims; `False` for denied claims |
| `liability_percentage` | INT | Required | No | % | `0` when denied; `100` when eligible |
| `recommendation` | VARCHAR(10) | Required | No | Enum | `APPROVE`, `DENY`, `REVIEW` |
| `notes` | TEXT | Required | No | Text | Explicit calculation summary and decision reason |
| `fraud_score` | DECIMAL(5,3) | Required | No | Score | `0.010`–`0.240` normal; `0.650`–`0.980` when `fraud_indicator == True` |

## 10. Reserve (`reserves`)

| Field | Type | Required | Unique | Units | Allowed Values / Validation Rule |
|---|---|---|---|---|---|
| `reserve_id` | VARCHAR(30) | Required (PK) | Yes | ID | `RSV-<claim_id>-NNN` |
| `claim_id` | VARCHAR(20) | Required (FK) | Yes | ID | 1:1 with non-denied reviewed `claims.claim_id` |
| `initial_reserve` | DECIMAL(12,2) | Required | No | USD | `80%`–`120%` of `claims.claim_amount` |
| `current_reserve` | DECIMAL(12,2) | Required | No | USD | `> 0.00` when `status == 'OPEN'`; `0.00` when `status == 'CLOSED'` |
| `reserve_type` | VARCHAR(10) | Required | No | Enum | `CASE`, `EXPENSE` |
| `set_date` | DATE | Required | No | Date | `> claims.submission_date` |
| `last_updated` | DATE | Required | No | Date | `>= set_date` |
| `set_by` | VARCHAR(15) | Required (FK) | No | ID | Adjuster ID |
| `currency` | CHAR(3) | Required | No | ISO | `USD` |
| `status` | VARCHAR(10) | Required | No | Enum | `OPEN`, `CLOSED` |

## 11. Settlement (`settlements`)

| Field | Type | Required | Unique | Units | Allowed Values / Validation Rule |
|---|---|---|---|---|---|
| `settlement_id` | VARCHAR(30) | Required (PK) | Yes | ID | `SET-<claim_id>-NNN` |
| `claim_id` | VARCHAR(20) | Required (FK) | No | ID | Must exist in `claims.claim_id` |
| `assessment_id` | VARCHAR(30) | Required (FK) | No | ID | Must exist in `assessments.assessment_id` |
| `settlement_amount` | DECIMAL(12,2) | Required | No | USD | Equals `assessments.eligible_amount` (except labelled `SETTLEMENT_EXCEEDS_LIMIT` anomaly cases) |
| `payment_method` | VARCHAR(20) | Required | No | Enum | `CHECK`, `ACH`, `WIRE_TRANSFER`, `DIRECT_DEPOSIT` |
| `payment_date` | DATE | Required | No | Date | `>= approved_date` |
| `approved_by` | VARCHAR(15) | Required (FK) | No | ID | Approving adjuster ID |
| `approved_date` | DATE | Required | No | Date | `> claims.submission_date` |
| `status` | VARCHAR(10) | Required | No | Enum | `PENDING`, `APPROVED`, `PAID`, `REJECTED` |
| `payee_name` | VARCHAR(100) | Required | No | Text | Derived from `customers.first_name || ' ' || customers.last_name` |
| `payee_customer_id` | VARCHAR(15) | Required (FK) | No | ID | Matches `claims.customer_id` |
| `notes` | TEXT | Optional | No | Text | Disbursement audit note |

## 12. ClaimAdjusterAssignment (`claim_adjuster_assignments` — N:M Bridge Table)

| Field | Type | Required | Unique | Units | Allowed Values / Validation Rule |
|---|---|---|---|---|---|
| `assignment_id` | VARCHAR(35) | Required (PK) | Yes | ID | `CAA-<claim_id>-NNN` |
| `claim_id` | VARCHAR(20) | Required (FK) | No | ID | Must exist in `claims.claim_id` |
| `adjuster_id` | VARCHAR(15) | Required (FK) | No | ID | Must exist in `adjusters.adjuster_id` |
| `assignment_role` | VARCHAR(25) | Required | No | Enum | `PRIMARY_ADJUSTER`, `SIU_INVESTIGATOR`, `SUPERVISOR_REVIEWER`, `CO_ADJUSTER` |
| `assigned_date` | DATE | Required | No | Date | `>= claims.submission_date` |
| `unassigned_date` | DATE | Optional | No | Date | Null while assignment is active |
| `is_active` | BOOLEAN | Required | No | Flag | `True` / `False` |
| `workload_weight` | DECIMAL(4,2) | Required | No | Weight | `1.00` primary, `0.50` SIU, `0.25` supervisor |

## 13. ClaimCoverageAllocation (`claim_coverage_allocations` — N:M Bridge Table)

| Field | Type | Required | Unique | Units | Allowed Values / Validation Rule |
|---|---|---|---|---|---|
| `allocation_id` | VARCHAR(35) | Required (PK) | Yes | ID | `CCA-<claim_id>-NNN` |
| `claim_id` | VARCHAR(20) | Required (FK) | No | ID | Must exist in `claims.claim_id` |
| `coverage_id` | VARCHAR(40) | Required (FK) | No | ID | Must belong to `claims.policy_id` |
| `is_primary` | BOOLEAN | Required | No | Flag | `True` for primary coverage line, `False` for secondary |
| `allocated_claimed_amount` | DECIMAL(12,2) | Required | No | USD | Per-claim sum strictly equals `claims.claim_amount` |
| `allocated_eligible_amount` | DECIMAL(12,2) | Required | No | USD | Per-claim sum strictly equals `assessments.eligible_amount` |
| `deductible_applied` | DECIMAL(12,2) | Required | No | USD | Deductible absorbed on this coverage line |
| `effective_date` | DATE | Required | No | Date | Matches `coverages.effective_date` |
| `expiration_date` | DATE | Required | No | Date | Matches `coverages.expiration_date` |

## 14. ClaimStateHistory (`claim_state_history` — Immutable Audit Log)

| Field | Type | Required | Unique | Units | Allowed Values / Validation Rule |
|---|---|---|---|---|---|
| `event_id` | VARCHAR(30) | Required (PK) | Yes | ID | `EVT-<claim_id>-NNN` |
| `claim_id` | VARCHAR(20) | Required (FK) | No | ID | Must exist in `claims.claim_id` |
| `from_status` | VARCHAR(20) | Optional | No | Enum | Null on initial `SUBMITTED` event; prior state otherwise |
| `to_status` | VARCHAR(20) | Required | No | Enum | Valid lifecycle state; final event matches `claims.status` |
| `changed_by` | VARCHAR(15) | Required | No | ID | `SYSTEM` or assigned `adjuster_id` |
| `change_reason` | TEXT | Required | No | Text | Explicit rule/audit explanation |
| `event_timestamp` | TIMESTAMP | Required | No | UTC | Strictly increasing per claim |
