-- SQLite DDL for loading output/sql/*.sql or the CSV/JSON tables.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS customers (
  customer_id TEXT PRIMARY KEY, first_name TEXT NOT NULL, last_name TEXT NOT NULL,
  date_of_birth TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL,
  address_line1 TEXT NOT NULL, city TEXT NOT NULL, state TEXT NOT NULL,
  zip_code TEXT NOT NULL, created_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ACTIVE','INACTIVE'))
);
CREATE TABLE IF NOT EXISTS adjusters (
  adjuster_id TEXT PRIMARY KEY, first_name TEXT NOT NULL, last_name TEXT NOT NULL,
  email TEXT NOT NULL, phone TEXT NOT NULL, specializations TEXT NOT NULL,
  license_number TEXT NOT NULL UNIQUE, hire_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ACTIVE','ON_LEAVE','INACTIVE')),
  max_concurrent_claims INTEGER NOT NULL CHECK(max_concurrent_claims > 0)
);
CREATE TABLE IF NOT EXISTS policies (
  policy_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(customer_id),
  product_type TEXT NOT NULL CHECK(product_type IN ('AUTO','HOME','HEALTH','LIFE','TRAVEL')),
  policy_number TEXT NOT NULL UNIQUE, effective_date TEXT NOT NULL, expiration_date TEXT NOT NULL,
  term_months INTEGER NOT NULL, premium_amount NUMERIC NOT NULL CHECK(premium_amount > 0),
  payment_frequency TEXT NOT NULL, status TEXT NOT NULL,
  cancellation_date TEXT, lapse_date TEXT, underwriting_status TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  CHECK(effective_date < expiration_date)
);
CREATE TABLE IF NOT EXISTS coverages (
  coverage_id TEXT PRIMARY KEY, policy_id TEXT NOT NULL REFERENCES policies(policy_id),
  coverage_type TEXT NOT NULL, coverage_limit NUMERIC NOT NULL CHECK(coverage_limit >= 0),
  deductible NUMERIC NOT NULL CHECK(deductible >= 0), co_pay_percentage INTEGER NOT NULL,
  exclusions TEXT, effective_date TEXT NOT NULL, expiration_date TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incidents (
  incident_id TEXT PRIMARY KEY, incident_type TEXT NOT NULL, incident_date TEXT NOT NULL,
  incident_time TEXT NOT NULL, description TEXT NOT NULL, location_city TEXT NOT NULL,
  location_state TEXT NOT NULL, severity TEXT NOT NULL, police_report_filed INTEGER NOT NULL,
  witnesses INTEGER NOT NULL CHECK(witnesses >= 0), product_category TEXT NOT NULL,
  reported_date TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
  claim_id TEXT PRIMARY KEY, policy_id TEXT NOT NULL REFERENCES policies(policy_id),
  customer_id TEXT NOT NULL REFERENCES customers(customer_id), incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
  claim_type TEXT NOT NULL CHECK(claim_type IN ('AUTO','HOME','HEALTH','LIFE','TRAVEL')),
  claim_amount NUMERIC NOT NULL CHECK(claim_amount > 0), submission_date TEXT NOT NULL,
  status TEXT NOT NULL, assigned_adjuster_id TEXT REFERENCES adjusters(adjuster_id),
  priority TEXT NOT NULL, denial_reason TEXT, fraud_indicator INTEGER NOT NULL,
  coverage_id TEXT REFERENCES coverages(coverage_id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claim_items (
  claim_item_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  item_description TEXT NOT NULL, item_category TEXT NOT NULL, claimed_amount NUMERIC NOT NULL,
  approved_amount NUMERIC, quantity INTEGER NOT NULL CHECK(quantity > 0),
  unit_cost NUMERIC NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
  document_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  document_type TEXT NOT NULL, file_name TEXT NOT NULL, file_size_kb INTEGER NOT NULL,
  upload_date TEXT NOT NULL, uploaded_by TEXT NOT NULL REFERENCES customers(customer_id), status TEXT NOT NULL,
  verification_date TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS assessments (
  assessment_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL UNIQUE REFERENCES claims(claim_id),
  adjuster_id TEXT NOT NULL REFERENCES adjusters(adjuster_id), assessment_date TEXT NOT NULL,
  assessed_amount NUMERIC NOT NULL, deductible_applied NUMERIC NOT NULL,
  eligible_amount NUMERIC NOT NULL, coverage_limit NUMERIC NOT NULL,
  coverage_verified INTEGER NOT NULL, liability_percentage INTEGER NOT NULL,
  recommendation TEXT NOT NULL, notes TEXT NOT NULL, fraud_score NUMERIC NOT NULL
);
CREATE TABLE IF NOT EXISTS reserves (
  reserve_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL UNIQUE REFERENCES claims(claim_id),
  initial_reserve NUMERIC NOT NULL, current_reserve NUMERIC NOT NULL, reserve_type TEXT NOT NULL,
  set_date TEXT NOT NULL, last_updated TEXT NOT NULL, set_by TEXT NOT NULL REFERENCES adjusters(adjuster_id),
  currency TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settlements (
  settlement_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  assessment_id TEXT REFERENCES assessments(assessment_id), settlement_amount NUMERIC NOT NULL,
  payment_method TEXT NOT NULL, payment_date TEXT NOT NULL, approved_by TEXT NOT NULL REFERENCES adjusters(adjuster_id),
  approved_date TEXT NOT NULL, status TEXT NOT NULL, payee_name TEXT NOT NULL,
  payee_customer_id TEXT REFERENCES customers(customer_id), notes TEXT
);
CREATE TABLE IF NOT EXISTS claim_state_history (
  event_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  from_status TEXT, to_status TEXT NOT NULL, changed_by TEXT NOT NULL,
  change_reason TEXT NOT NULL, event_timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_claims_policy ON claims(policy_id);
CREATE INDEX IF NOT EXISTS idx_claims_incident ON claims(incident_id);
CREATE INDEX IF NOT EXISTS idx_documents_claim ON documents(claim_id);
CREATE INDEX IF NOT EXISTS idx_events_claim_time ON claim_state_history(claim_id,event_timestamp);
