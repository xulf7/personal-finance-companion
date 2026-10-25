CREATE TABLE md_assets (
    asset_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    asset_type TEXT NOT NULL CHECK (asset_type IN ('stock', 'fund', 'index')),
    market TEXT NOT NULL,
    code TEXT NOT NULL,
    UNIQUE (asset_type, market, code)
);

CREATE TABLE md_cache (
    asset_id TEXT NOT NULL REFERENCES md_assets(asset_id),
    metric TEXT NOT NULL CHECK (metric IN ('price', 'nav', 'index_level')),
    provider_id TEXT NOT NULL,
    is_simulated INTEGER NOT NULL CHECK (is_simulated IN (0, 1)),
    value TEXT NOT NULL CHECK (length(value) > 0),
    currency TEXT,
    unit TEXT NOT NULL,
    data_time_kind TEXT NOT NULL CHECK (data_time_kind IN ('date', 'datetime')),
    data_time TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL CHECK (expires_at > fetched_at),
    revision INTEGER NOT NULL CHECK (revision > 0),
    PRIMARY KEY (asset_id, metric, provider_id, is_simulated),
    CHECK (
        (metric = 'price' AND unit = 'currency_per_share' AND currency IS NOT NULL AND length(currency) = 3) OR
        (metric = 'nav' AND unit = 'currency_per_unit' AND currency IS NOT NULL AND length(currency) = 3) OR
        (metric = 'index_level' AND unit = 'points' AND currency IS NULL)
    )
);
