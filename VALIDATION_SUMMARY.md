# Validation & Reproducibility Summary (Advanced Edition v2.0)

## 1. Automated Validation Results (`python3 validate.py`)

- **Overall Status**: `PASS`
- **Total Checks Executed**: `114`
- **Passed**: `114`
- **Failed**: `0`
- **Warnings**: `0`

### Breakdown by Validation Section

| Section | Checks | Status | Key Verification Scope |
|---|---:|---|---|
| 1. Primary-Key Uniqueness | 14 | `14 PASS` | All 11 core entities + 2 N:M bridge tables + `claim_state_history` |
| 2. Foreign-Key Integrity | 23 | `23 PASS` | All 23 parent-child and bridge FK relationships (`0` orphans) |
| 3. Join & N:M Bridge Consistency | 7 | `7 PASS` | Cross-table product/customer/date agreement + N:M bridge role & policy checks |
| 4. Allowed Enum Values | 21 | `21 PASS` | All 21 status, product, priority, tier, and payment method columns |
| 5. Date Ordering & Lifecycle | 5 | `5 PASS` | `effective_date < expiration_date`, `incident <= reported <= submission`, `approved <= payment` |
| 6. Exact Bottom-Up Arithmetic | 8 | `8 PASS` | `unit_cost * qty == claimed_amount`, `SUM(items) == claim_amount`, `SUM(approved_items) == assessed_amount`, `eligible_amount == 0` when denied, N:M allocation sum |
| 7. Lifecycle & Gating Rules | 5 | `5 PASS` | Valid state transitions, explicit `denial_reason`, final state match, document gating, SIU fraud gating |
| 8. Duplicate Reconciliation | 2 | `2 PASS` | All duplicate incidents and multi-settlement claims reconciled 100% with `ground_truth.csv` |
| 9. Bi-Directional Ground Truth | 18 | `18 PASS` | All 6 required edge-case classes verified + zero unlabelled early/cancelled claims in baseline |
| 10. Zero-Leakage Features | 2 | `2 PASS` | `features/claims_input.csv` has 1,000 rows and 0 leaked outcome/fraud columns |
| 11. Volume & Distribution | 7 | `7 PASS` | 1,500 customers, 50 adjusters, 2,000 policies, 1,000 claims across 5 product types |
| 12. Manifest SHA-256 Integrity | 1 | `1 PASS` | All 46 exported CSV, JSON, SQL, feature, and ground-truth files match `manifest.json` SHA-256 hashes |

## 2. Fixed-Seed Reproducibility (`python3 generate.py --verify`)

- **Default Seed**: `20261022`
- **Full Dataset Scale (`--scale full`)**:
  - **Run 1 SHA-256 Checksum**: `a4a9ada70bbee7ead5781fe2f0b5a4ee0ec5b1e91b5d4c580e4d1f9149082e0a`
  - **Run 2 SHA-256 Checksum**: `a4a9ada70bbee7ead5781fe2f0b5a4ee0ec5b1e91b5d4c580e4d1f9149082e0a`
  - **Result**: `PASS — checksums match identically`
