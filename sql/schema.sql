CREATE TABLE IF NOT EXISTS sources (
    source_id VARCHAR PRIMARY KEY,
    title VARCHAR NOT NULL,
    publisher VARCHAR NOT NULL,
    source_type VARCHAR NOT NULL,
    url VARCHAR NOT NULL,
    disclosed_at DATE NOT NULL,
    accessed_at DATE NOT NULL,
    first_available_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ,
    revision_id VARCHAR,
    superseded_at TIMESTAMPTZ
);

ALTER TABLE sources ADD COLUMN IF NOT EXISTS first_available_at TIMESTAMPTZ;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS revision_id VARCHAR;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS company_master (
    company_id VARCHAR PRIMARY KEY,
    company_name VARCHAR NOT NULL,
    country VARCHAR NOT NULL,
    industry_layer VARCHAR NOT NULL,
    primary_business VARCHAR NOT NULL,
    fiscal_year_end VARCHAR
);

CREATE TABLE IF NOT EXISTS security_master (
    security_id VARCHAR PRIMARY KEY,
    company_id VARCHAR NOT NULL,
    ticker VARCHAR NOT NULL,
    market VARCHAR NOT NULL,
    currency VARCHAR NOT NULL,
    security_type VARCHAR NOT NULL,
    is_primary BOOLEAN NOT NULL,
    UNIQUE (ticker, market)
);

CREATE TABLE IF NOT EXISTS company_research_snapshot (
    company_id VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    core_product VARCHAR NOT NULL,
    major_customers VARCHAR NOT NULL,
    ai_revenue_exposure VARCHAR NOT NULL,
    exposure_basis VARCHAR NOT NULL,
    current_thesis VARCHAR NOT NULL,
    strongest_bear_case VARCHAR NOT NULL,
    source_id VARCHAR NOT NULL,
    confidence DOUBLE NOT NULL,
    first_available_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ,
    revision_id VARCHAR,
    superseded_at TIMESTAMPTZ,
    PRIMARY KEY (company_id, as_of_date)
);

ALTER TABLE company_research_snapshot ADD COLUMN IF NOT EXISTS first_available_at TIMESTAMPTZ;
ALTER TABLE company_research_snapshot ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ;
ALTER TABLE company_research_snapshot ADD COLUMN IF NOT EXISTS revision_id VARCHAR;
ALTER TABLE company_research_snapshot ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS supply_chain_edges (
    edge_id VARCHAR PRIMARY KEY,
    source_company_id VARCHAR NOT NULL,
    relation_type VARCHAR NOT NULL,
    target_company_id VARCHAR NOT NULL,
    product VARCHAR NOT NULL,
    valid_from DATE,
    valid_to DATE,
    disclosed_at DATE NOT NULL,
    economic_exposure VARCHAR NOT NULL,
    confidence DOUBLE NOT NULL,
    source_id VARCHAR NOT NULL,
    first_available_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ,
    revision_id VARCHAR,
    superseded_at TIMESTAMPTZ
);

ALTER TABLE supply_chain_edges ADD COLUMN IF NOT EXISTS first_available_at TIMESTAMPTZ;
ALTER TABLE supply_chain_edges ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ;
ALTER TABLE supply_chain_edges ADD COLUMN IF NOT EXISTS revision_id VARCHAR;
ALTER TABLE supply_chain_edges ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS source_evidence (
    evidence_id VARCHAR PRIMARY KEY,
    edge_id VARCHAR NOT NULL UNIQUE,
    source_id VARCHAR NOT NULL,
    source_locator VARCHAR NOT NULL,
    evidence_summary VARCHAR NOT NULL,
    archived_path VARCHAR NOT NULL,
    content_sha256 VARCHAR NOT NULL,
    mime_type VARCHAR NOT NULL,
    archived_at DATE NOT NULL,
    auditor_result VARCHAR NOT NULL,
    auditor_note VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS hypotheses (
    hypothesis_id VARCHAR PRIMARY KEY,
    title VARCHAR NOT NULL,
    statement VARCHAR NOT NULL,
    mechanism VARCHAR NOT NULL,
    strongest_bear_case VARCHAR NOT NULL,
    falsification_condition VARCHAR NOT NULL,
    status VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS company_exposures (
    company_id VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    capex_exposure DOUBLE,
    bottleneck DOUBLE,
    earnings_revision DOUBLE,
    profit_capture DOUBLE,
    priced_in DOUBLE,
    confidence DOUBLE,
    source_id VARCHAR,
    analyst_note VARCHAR,
    first_available_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ,
    revision_id VARCHAR,
    superseded_at TIMESTAMPTZ,
    PRIMARY KEY (company_id, as_of_date)
);

ALTER TABLE company_exposures ADD COLUMN IF NOT EXISTS first_available_at TIMESTAMPTZ;
ALTER TABLE company_exposures ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ;
ALTER TABLE company_exposures ADD COLUMN IF NOT EXISTS revision_id VARCHAR;
ALTER TABLE company_exposures ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS capex_events (
    event_id VARCHAR PRIMARY KEY,
    company_id VARCHAR NOT NULL,
    fiscal_period VARCHAR NOT NULL,
    event_type VARCHAR NOT NULL,
    guidance_low_millions DOUBLE,
    guidance_high_millions DOUBLE,
    currency VARCHAR NOT NULL,
    direction VARCHAR NOT NULL,
    source_id VARCHAR NOT NULL,
    first_available_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    revision_id VARCHAR NOT NULL,
    superseded_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS fundamental_signals (
    signal_id VARCHAR PRIMARY KEY,
    company_id VARCHAR NOT NULL,
    snapshot_at TIMESTAMPTZ NOT NULL,
    metric_name VARCHAR NOT NULL,
    metric_change DOUBLE,
    source_id VARCHAR NOT NULL,
    first_available_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    revision_id VARCHAR NOT NULL,
    superseded_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS expectation_signals (
    signal_id VARCHAR PRIMARY KEY,
    security_id VARCHAR NOT NULL,
    snapshot_at TIMESTAMPTZ NOT NULL,
    revenue_revision DOUBLE,
    eps_revision DOUBLE,
    valuation_multiple DOUBLE,
    source_id VARCHAR,
    first_available_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    revision_id VARCHAR NOT NULL,
    superseded_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS price_signals (
    signal_id VARCHAR PRIMARY KEY,
    security_id VARCHAR NOT NULL,
    snapshot_at TIMESTAMPTZ NOT NULL,
    price_return DOUBLE,
    source_id VARCHAR,
    first_available_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    revision_id VARCHAR NOT NULL,
    superseded_at TIMESTAMPTZ
);

DROP VIEW IF EXISTS opportunity_scores;
DROP VIEW IF EXISTS initial_universe;

CREATE OR REPLACE MACRO latest_company_research(cutoff) AS TABLE
SELECT * EXCLUDE (pit_rank)
FROM (
    SELECT r.*,
           row_number() OVER (
               PARTITION BY r.company_id
               ORDER BY r.as_of_date DESC, r.first_available_at DESC, r.revision_id DESC
           ) AS pit_rank
    FROM company_research_snapshot r
    WHERE r.first_available_at <= cutoff
      AND r.as_of_date <= CAST(cutoff AS DATE)
      AND (r.superseded_at IS NULL OR r.superseded_at > cutoff)
)
WHERE pit_rank = 1;

CREATE OR REPLACE MACRO opportunity_scores_as_of(cutoff) AS TABLE
SELECT
    e.company_id,
    c.company_name,
    c.industry_layer,
    e.as_of_date,
    0.25 * e.capex_exposure
      + 0.20 * e.bottleneck
      + 0.20 * e.earnings_revision
      + 0.20 * e.profit_capture
      - 0.15 * e.priced_in AS opportunity_score,
    e.confidence,
    e.analyst_note,
    e.first_available_at,
    e.revision_id
FROM company_exposures e
JOIN company_master c USING (company_id)
WHERE e.first_available_at <= cutoff
  AND e.as_of_date <= CAST(cutoff AS DATE)
  AND (e.superseded_at IS NULL OR e.superseded_at > cutoff)
  AND e.capex_exposure IS NOT NULL
  AND e.bottleneck IS NOT NULL
  AND e.earnings_revision IS NOT NULL
  AND e.profit_capture IS NOT NULL
  AND e.priced_in IS NOT NULL
QUALIFY row_number() OVER (
    PARTITION BY e.company_id
    ORDER BY e.as_of_date DESC, e.first_available_at DESC, e.revision_id DESC
) = 1;

CREATE OR REPLACE MACRO initial_universe_as_of(cutoff) AS TABLE
SELECT
    c.company_name AS company,
    string_agg(s.ticker, ' / ' ORDER BY s.is_primary DESC, s.market) AS security_code,
    string_agg(s.market, ' / ' ORDER BY s.is_primary DESC, s.market) AS market,
    c.industry_layer,
    r.core_product,
    r.major_customers,
    r.ai_revenue_exposure,
    r.exposure_basis,
    src.url AS relationship_source,
    src.disclosed_at,
    r.first_available_at,
    r.revision_id,
    r.current_thesis,
    r.strongest_bear_case,
    r.confidence
FROM company_master c
JOIN security_master s USING (company_id)
JOIN latest_company_research(cutoff) r USING (company_id)
JOIN sources src USING (source_id)
WHERE src.first_available_at <= cutoff
  AND (src.superseded_at IS NULL OR src.superseded_at > cutoff)
GROUP BY ALL;
