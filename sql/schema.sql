CREATE TABLE IF NOT EXISTS sources (
    source_id VARCHAR PRIMARY KEY,
    title VARCHAR NOT NULL,
    publisher VARCHAR NOT NULL,
    source_type VARCHAR NOT NULL,
    url VARCHAR NOT NULL,
    disclosed_at DATE NOT NULL,
    accessed_at DATE NOT NULL
);

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
    PRIMARY KEY (company_id, as_of_date)
);

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
    source_id VARCHAR NOT NULL
);

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
    PRIMARY KEY (company_id, as_of_date)
);

CREATE OR REPLACE VIEW opportunity_scores AS
WITH latest_exposures AS (
    SELECT *
    FROM company_exposures
    WHERE as_of_date <= current_date
    QUALIFY row_number() OVER (PARTITION BY company_id ORDER BY as_of_date DESC) = 1
)
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
    e.analyst_note
FROM latest_exposures e
JOIN company_master c USING (company_id)
WHERE e.capex_exposure IS NOT NULL
  AND e.bottleneck IS NOT NULL
  AND e.earnings_revision IS NOT NULL
  AND e.profit_capture IS NOT NULL
  AND e.priced_in IS NOT NULL;

CREATE OR REPLACE VIEW initial_universe AS
WITH latest_research AS (
    SELECT r.*
    FROM company_research_snapshot r
    JOIN sources src USING (source_id)
    WHERE r.as_of_date <= current_date
      AND src.disclosed_at <= current_date
    QUALIFY row_number() OVER (PARTITION BY r.company_id ORDER BY r.as_of_date DESC) = 1
)
SELECT
    c.company_name AS company,
    string_agg(s.ticker, ' / ' ORDER BY s.is_primary DESC, s.market) AS security_code,
    string_agg(s.market, ' / ' ORDER BY s.is_primary DESC, s.market) AS market,
    c.industry_layer,
    r.core_product,
    r.major_customers,
    r.ai_revenue_exposure,
    src.url AS relationship_source,
    src.disclosed_at,
    r.as_of_date,
    r.current_thesis,
    r.strongest_bear_case,
    r.confidence
FROM company_master c
JOIN security_master s USING (company_id)
JOIN latest_research r USING (company_id)
JOIN sources src USING (source_id)
GROUP BY ALL;
