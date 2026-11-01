-- Unified SQLite DDL for the 14-Table Insurance Claims Data Model (v2.0)
-- Includes 11 Core Entities + 2 N:M Bridge Tables + 1 Immutable Event Log
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS customers (
  customer_id TEXT PRIMARY KEY,
  first_name TEXT NOT NULL,
  last_name TEXT NOT NULL,
  date_of_birth TEXT NOT NULL,
  email TEXT NOT NULL,
  phone TEXT NOT NULL,
  address_line1 TEXT NOT NULL,
  city TEXT NOT NULL,
  state TEXT NOT NULL,
  zip_code TEXT NOT NULL,
  risk_tier TEXT NOT NULL DEFAULT 'STANDARD' CHECK(risk_tier IN ('LOW','STANDARD','ELEVATED')),
  created_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ACTIVE','INACTIVE'))
);

CREATE TABLE IF NOT EXISTS adjusters (
  adjuster_id TEXT PRIMARY KEY,
  first_name TEXT NOT NULL,
  last_name TEXT NOT NULL,
  email TEXT NOT NULL,
  phone TEXT NOT NULL,
  specializations TEXT NOT NULL,
  role_tier TEXT NOT NULL DEFAULT 'STANDARD_ADJUSTER' CHECK(role_tier IN ('SENIOR_ADJUSTER','STANDARD_ADJUSTER','SIU_SPECIALIST')),
  license_number TEXT NOT NULL UNIQUE,
  hire_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ACTIVE','ON_LEAVE','INACTIVE')),
  max_concurrent_claims INTEGER NOT NULL CHECK(max_concurrent_claims > 0)
);

CREATE TABLE IF NOT EXISTS policies (
  policy_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES customers(customer_id),
  product_type TEXT NOT NULL CHECK(product_type IN ('AUTO','HOME','HEALTH','LIFE','TRAVEL')),
  policy_number TEXT NOT NULL UNIQUE,
  effective_date TEXT NOT NULL,
  expiration_date TEXT NOT NULL,
  term_months INTEGER NOT NULL CHECK(term_months > 0),
  premium_amount NUMERIC NOT NULL CHECK(premium_amount > 0),
  payment_frequency TEXT NOT NULL CHECK(payment_frequency IN ('MONTHLY','QUARTERLY','SEMI_ANNUAL','ANNUAL')),
  status TEXT NOT NULL CHECK(status IN ('ACTIVE','EXPIRED','CANCELLED','LAPSED')),
  cancellation_date TEXT,
  lapse_date TEXT,
  underwriting_status TEXT NOT NULL CHECK(underwriting_status IN ('APPROVED','CONDITIONAL','DECLINED')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK(effective_date < expiration_date)
);

CREATE TABLE IF NOT EXISTS coverages (
  coverage_id TEXT PRIMARY KEY,
  policy_id TEXT NOT NULL REFERENCES policies(policy_id),
  coverage_type TEXT NOT NULL,
  coverage_limit NUMERIC NOT NULL CHECK(coverage_limit >= 0),
  deductible NUMERIC NOT NULL CHECK(deductible >= 0),
  co_pay_percentage INTEGER NOT NULL CHECK(co_pay_percentage >= 0 AND co_pay_percentage <= 100),
  exclusions TEXT,
  effective_date TEXT NOT NULL,
  expiration_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ACTIVE','INACTIVE'))
);

CREATE TABLE IF NOT EXISTS incidents (
  incident_id TEXT PRIMARY KEY,
  incident_type TEXT NOT NULL,
  incident_date TEXT NOT NULL,
  incident_time TEXT NOT NULL,
  description TEXT NOT NULL,
  location_city TEXT NOT NULL,
  location_state TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
  police_report_filed INTEGER NOT NULL,
  witnesses INTEGER NOT NULL CHECK(witnesses >= 0),
  product_category TEXT NOT NULL CHECK(product_category IN ('AUTO','HOME','HEALTH','LIFE','TRAVEL')),
  reported_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
  claim_id TEXT PRIMARY KEY,
  policy_id TEXT NOT NULL REFERENCES policies(policy_id),
  customer_id TEXT NOT NULL REFERENCES customers(customer_id),
  incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
  claim_type TEXT NOT NULL CHECK(claim_type IN ('AUTO','HOME','HEALTH','LIFE','TRAVEL')),
  claim_amount NUMERIC NOT NULL CHECK(claim_amount > 0),
  submission_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('SUBMITTED','PENDING_DOCUMENTS','UNDER_REVIEW','SIU_INVESTIGATION','APPROVED','DENIED','SETTLED','CLOSED')),
  assigned_adjuster_id TEXT REFERENCES adjusters(adjuster_id),
  priority TEXT NOT NULL CHECK(priority IN ('LOW','MEDIUM','HIGH','URGENT')),
  denial_reason TEXT,
  fraud_indicator INTEGER NOT NULL DEFAULT 0,
  coverage_id TEXT REFERENCES coverages(coverage_id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_items (
  claim_item_id TEXT PRIMARY KEY,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  item_description TEXT NOT NULL,
  item_category TEXT NOT NULL CHECK(item_category IN ('AUTO','HOME','HEALTH','LIFE','TRAVEL')),
  claimed_amount NUMERIC NOT NULL CHECK(claimed_amount > 0),
  approved_amount NUMERIC,
  quantity INTEGER NOT NULL CHECK(quantity > 0),
  unit_cost NUMERIC NOT NULL CHECK(unit_cost > 0),
  status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','DENIED'))
);

CREATE TABLE IF NOT EXISTS documents (
  document_id TEXT PRIMARY KEY,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  document_type TEXT NOT NULL,
  file_name TEXT NOT NULL,
  file_size_kb INTEGER NOT NULL CHECK(file_size_kb >= 0),
  upload_date TEXT NOT NULL,
  uploaded_by TEXT NOT NULL REFERENCES customers(customer_id),
  status TEXT NOT NULL CHECK(status IN ('REQUESTED','RECEIVED','VERIFIED','REJECTED')),
  verification_date TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS assessments (
  assessment_id TEXT PRIMARY KEY,
  claim_id TEXT NOT NULL UNIQUE REFERENCES claims(claim_id),
  adjuster_id TEXT NOT NULL REFERENCES adjusters(adjuster_id),
  assessment_date TEXT NOT NULL,
  assessed_amount NUMERIC NOT NULL CHECK(assessed_amount >= 0),
  deductible_applied NUMERIC NOT NULL CHECK(deductible_applied >= 0),
  co_pay_percentage INTEGER NOT NULL DEFAULT 0 CHECK(co_pay_percentage >= 0 AND co_pay_percentage <= 100),
  eligible_amount NUMERIC NOT NULL CHECK(eligible_amount >= 0),
  coverage_limit NUMERIC NOT NULL CHECK(coverage_limit >= 0),
  coverage_verified INTEGER NOT NULL,
  liability_percentage INTEGER NOT NULL CHECK(liability_percentage >= 0 AND liability_percentage <= 100),
  recommendation TEXT NOT NULL CHECK(recommendation IN ('APPROVE','DENY','REVIEW')),
  notes TEXT NOT NULL,
  fraud_score NUMERIC NOT NULL CHECK(fraud_score >= 0 AND fraud_score <= 1)
);

CREATE TABLE IF NOT EXISTS reserves (
  reserve_id TEXT PRIMARY KEY,
  claim_id TEXT NOT NULL UNIQUE REFERENCES claims(claim_id),
  initial_reserve NUMERIC NOT NULL CHECK(initial_reserve >= 0),
  current_reserve NUMERIC NOT NULL CHECK(current_reserve >= 0),
  reserve_type TEXT NOT NULL CHECK(reserve_type IN ('CASE','EXPENSE')),
  set_date TEXT NOT NULL,
  last_updated TEXT NOT NULL,
  set_by TEXT NOT NULL REFERENCES adjusters(adjuster_id),
  currency TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('OPEN','CLOSED'))
);

CREATE TABLE IF NOT EXISTS settlements (
  settlement_id TEXT PRIMARY KEY,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  assessment_id TEXT REFERENCES assessments(assessment_id),
  settlement_amount NUMERIC NOT NULL CHECK(settlement_amount >= 0),
  payment_method TEXT NOT NULL CHECK(payment_method IN ('CHECK','ACH','WIRE_TRANSFER','DIRECT_DEPOSIT')),
  payment_date TEXT NOT NULL,
  approved_by TEXT NOT NULL REFERENCES adjusters(adjuster_id),
  approved_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','PAID','REJECTED')),
  payee_name TEXT NOT NULL,
  payee_customer_id TEXT REFERENCES customers(customer_id),
  notes TEXT
);

-- N:M Bridge Table 1: Claim <-> Adjuster Assignments
CREATE TABLE IF NOT EXISTS claim_adjuster_assignments (
  assignment_id TEXT PRIMARY KEY,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  adjuster_id TEXT NOT NULL REFERENCES adjusters(adjuster_id),
  assignment_role TEXT NOT NULL CHECK(assignment_role IN ('PRIMARY_ADJUSTER','SIU_INVESTIGATOR','SUPERVISOR_REVIEWER','CO_ADJUSTER')),
  assigned_date TEXT NOT NULL,
  unassigned_date TEXT,
  is_active INTEGER NOT NULL DEFAULT 1,
  workload_weight NUMERIC NOT NULL CHECK(workload_weight > 0)
);

-- N:M Bridge Table 2: Claim <-> Coverage Allocations
CREATE TABLE IF NOT EXISTS claim_coverage_allocations (
  allocation_id TEXT PRIMARY KEY,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  coverage_id TEXT NOT NULL REFERENCES coverages(coverage_id),
  is_primary INTEGER NOT NULL DEFAULT 1,
  allocated_claimed_amount NUMERIC NOT NULL CHECK(allocated_claimed_amount >= 0),
  allocated_eligible_amount NUMERIC NOT NULL CHECK(allocated_eligible_amount >= 0),
  deductible_applied NUMERIC NOT NULL CHECK(deductible_applied >= 0),
  effective_date TEXT NOT NULL,
  expiration_date TEXT NOT NULL
);

-- Immutable Event History Log
CREATE TABLE IF NOT EXISTS claim_state_history (
  event_id TEXT PRIMARY KEY,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  from_status TEXT,
  to_status TEXT NOT NULL,
  changed_by TEXT NOT NULL,
  change_reason TEXT NOT NULL,
  event_timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_claims_policy ON claims(policy_id);
CREATE INDEX IF NOT EXISTS idx_claims_incident ON claims(incident_id);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);
CREATE INDEX IF NOT EXISTS idx_documents_claim ON documents(claim_id);
CREATE INDEX IF NOT EXISTS idx_items_claim ON claim_items(claim_id);
CREATE INDEX IF NOT EXISTS idx_caa_claim ON claim_adjuster_assignments(claim_id);
CREATE INDEX IF NOT EXISTS idx_caa_adjuster ON claim_adjuster_assignments(adjuster_id);
CREATE INDEX IF NOT EXISTS idx_cca_claim ON claim_coverage_allocations(claim_id);
CREATE INDEX IF NOT EXISTS idx_events_claim_time ON claim_state_history(claim_id, event_timestamp);
