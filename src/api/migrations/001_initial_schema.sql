-- Jackdaw Sentry - Initial Database Schema
-- PostgreSQL Compliance Database Schema

-- =============================================================================
-- Users Table
-- =============================================================================
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(100),
    role VARCHAR(20) DEFAULT 'analyst' CHECK (role IN ('admin', 'analyst', 'investigator', 'auditor', 'viewer')),
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP WITH TIME ZONE,
    gdpr_consent_given BOOLEAN DEFAULT false,
    gdpr_consent_date TIMESTAMP WITH TIME ZONE,
    data_retention_days INTEGER DEFAULT 2555
);

-- =============================================================================
-- Investigations Table
-- =============================================================================
CREATE TABLE IF NOT EXISTS investigations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_number VARCHAR(50) UNIQUE NOT NULL,
    title VARCHAR(200) NOT NULL,
    description TEXT,
    status VARCHAR(20) DEFAULT 'open',
    priority VARCHAR(10) DEFAULT 'medium',
    assigned_to UUID REFERENCES users(id) ON DELETE SET NULL,
    created_by UUID REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP WITH TIME ZONE,
    blockchain VARCHAR(50),
    tags TEXT[]
);

-- =============================================================================
-- Evidence Table
-- =============================================================================
CREATE TABLE IF NOT EXISTS evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    investigation_id UUID REFERENCES investigations(id) ON DELETE CASCADE,
    evidence_type VARCHAR(50) NOT NULL,
    title VARCHAR(200) NOT NULL,
    description TEXT,
    file_path VARCHAR(500),
    file_hash VARCHAR(64),
    blockchain_address VARCHAR(100),
    transaction_hash VARCHAR(100),
    collected_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    collected_by UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- Compliance Reports Table
-- =============================================================================
CREATE TABLE IF NOT EXISTS compliance_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_number VARCHAR(50) UNIQUE NOT NULL,
    report_type VARCHAR(50) NOT NULL,
    blockchain VARCHAR(50),
    address VARCHAR(100),
    risk_score DECIMAL(5,2),
    compliance_flags TEXT[],
    sanctions_match BOOLEAN DEFAULT false,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    report_data JSONB
);

-- =============================================================================
-- Transactions Table
-- =============================================================================
CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_hash VARCHAR(100) UNIQUE NOT NULL,
    blockchain VARCHAR(50) NOT NULL,
    block_number BIGINT,
    block_hash VARCHAR(100),
    from_address VARCHAR(100) NOT NULL,
    to_address VARCHAR(100),
    amount DECIMAL(20,8),
    amount_usd DECIMAL(20,8),
    gas_used BIGINT,
    gas_price DECIMAL(20,8),
    transaction_fee DECIMAL(20,8),
    timestamp TIMESTAMP WITH TIME ZONE,
    status VARCHAR(20) DEFAULT 'confirmed',
    risk_indicators TEXT[],
    compliance_flags TEXT[],
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- Addresses Table
-- =============================================================================
CREATE TABLE IF NOT EXISTS addresses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    address VARCHAR(100) NOT NULL,
    blockchain VARCHAR(50) NOT NULL,
    UNIQUE (address, blockchain),
    label VARCHAR(100),
    risk_score DECIMAL(5,2),
    transaction_count BIGINT DEFAULT 0,
    total_received DECIMAL(20,8) DEFAULT 0,
    total_sent DECIMAL(20,8) DEFAULT 0,
    first_seen TIMESTAMP WITH TIME ZONE,
    last_seen TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_contract BOOLEAN DEFAULT false,
    tags TEXT[],
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- Sanctions Lists Table
-- =============================================================================
CREATE TABLE IF NOT EXISTS sanctions_lists (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    list_name VARCHAR(100) NOT NULL,
    list_type VARCHAR(50) NOT NULL,
    source VARCHAR(100) NOT NULL,
    version VARCHAR(20),
    download_url VARCHAR(500),
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT true
);

-- =============================================================================
-- Sanctions Entries Table
-- =============================================================================
CREATE TABLE IF NOT EXISTS sanctions_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    list_id UUID REFERENCES sanctions_lists(id) ON DELETE CASCADE,
    address VARCHAR(100) NOT NULL,
    name VARCHAR(200),
    identification_type VARCHAR(50),
    identification_number VARCHAR(100),
    country VARCHAR(3),
    date_added DATE,
    date_removed DATE,
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- Risk Scoring Models Table
-- =============================================================================
CREATE TABLE IF NOT EXISTS risk_scoring_models (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_name VARCHAR(100) UNIQUE NOT NULL,
    model_type VARCHAR(50) NOT NULL,
    version VARCHAR(20),
    description TEXT,
    parameters JSONB,
    is_active BOOLEAN DEFAULT true,
    accuracy_score DECIMAL(5,2),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- Audit Logs Table (GDPR Compliance)
-- =============================================================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50),
    resource_id VARCHAR(100),
    ip_address INET,
    user_agent TEXT,
    success BOOLEAN DEFAULT true,
    error_message TEXT,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    session_id VARCHAR(100),
    gdpr_data_accessed JSONB,
    retention_required_days INTEGER DEFAULT 2555
);

-- =============================================================================
-- Indexes for Performance
-- =============================================================================

-- Users indexes
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active);

-- Investigations indexes
CREATE INDEX IF NOT EXISTS idx_investigations_status ON investigations(status);
CREATE INDEX IF NOT EXISTS idx_investigations_assigned_to ON investigations(assigned_to);
CREATE INDEX IF NOT EXISTS idx_investigations_created_at ON investigations(created_at);

-- Evidence indexes
CREATE INDEX IF NOT EXISTS idx_evidence_investigation_id ON evidence(investigation_id);
CREATE INDEX IF NOT EXISTS idx_evidence_type ON evidence(evidence_type);

-- Compliance reports indexes
CREATE INDEX IF NOT EXISTS idx_compliance_reports_address ON compliance_reports(address);
CREATE INDEX IF NOT EXISTS idx_compliance_reports_blockchain ON compliance_reports(blockchain);
CREATE INDEX IF NOT EXISTS idx_compliance_reports_created_at ON compliance_reports(created_at);

-- Transactions indexes
CREATE INDEX IF NOT EXISTS idx_transactions_hash ON transactions(transaction_hash);
CREATE INDEX IF NOT EXISTS idx_transactions_from_address ON transactions(from_address);
CREATE INDEX IF NOT EXISTS idx_transactions_to_address ON transactions(to_address);
CREATE INDEX IF NOT EXISTS idx_transactions_blockchain ON transactions(blockchain);
CREATE INDEX IF NOT EXISTS idx_transactions_timestamp ON transactions(timestamp);
CREATE INDEX IF NOT EXISTS idx_transactions_amount_usd ON transactions(amount_usd DESC);

-- Addresses indexes
CREATE INDEX IF NOT EXISTS idx_addresses_address ON addresses(address);
CREATE INDEX IF NOT EXISTS idx_addresses_blockchain ON addresses(blockchain);
CREATE INDEX IF NOT EXISTS idx_addresses_risk_score ON addresses(risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_addresses_last_seen ON addresses(last_seen DESC);

-- Sanctions entries indexes
CREATE INDEX IF NOT EXISTS idx_sanctions_address ON sanctions_entries(address);
CREATE INDEX IF NOT EXISTS idx_sanctions_list_id ON sanctions_entries(list_id);
CREATE INDEX IF NOT EXISTS idx_sanctions_active ON sanctions_entries(date_removed IS NULL);

-- Audit logs indexes
CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_session_id ON audit_logs(session_id);

-- =============================================================================
-- Triggers for GDPR Compliance
-- =============================================================================

-- Function to automatically set updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply triggers to tables with updated_at columns
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_investigations_updated_at BEFORE UPDATE ON investigations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_evidence_updated_at BEFORE UPDATE ON evidence
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_compliance_reports_updated_at BEFORE UPDATE ON compliance_reports
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_transactions_updated_at BEFORE UPDATE ON transactions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_addresses_updated_at BEFORE UPDATE ON addresses
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_sanctions_entries_updated_at BEFORE UPDATE ON sanctions_entries
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_risk_scoring_models_updated_at BEFORE UPDATE ON risk_scoring_models
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
