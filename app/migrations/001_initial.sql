CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL CHECK (version >= 0),
    updated_at TEXT NOT NULL,
    currency TEXT CHECK (currency IS NULL OR length(currency) = 3),
    income TEXT,
    expenses TEXT,
    cash TEXT,
    debt TEXT,
    savings_budget TEXT,
    investment_budget TEXT,
    risk_preference TEXT CHECK (risk_preference IN ('low', 'medium', 'high'))
);

INSERT INTO profile (id, version, updated_at)
VALUES (1, 0, strftime('%Y-%m-%dT%H:%M:%f000Z', 'now'));

CREATE TABLE goals (
    id TEXT PRIMARY KEY,
    profile_id INTEGER NOT NULL DEFAULT 1 CHECK (profile_id = 1) REFERENCES profile(id),
    name TEXT NOT NULL,
    target_amount TEXT,
    currency TEXT NOT NULL CHECK (length(currency) = 3),
    target_date TEXT
);

CREATE TABLE holdings (
    id TEXT PRIMARY KEY,
    profile_id INTEGER NOT NULL DEFAULT 1 CHECK (profile_id = 1) REFERENCES profile(id),
    asset_code TEXT NOT NULL,
    quantity TEXT,
    unit_cost TEXT,
    currency TEXT NOT NULL CHECK (length(currency) = 3)
);

CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content TEXT NOT NULL CHECK (length(trim(content)) > 0),
    created_at TEXT NOT NULL,
    UNIQUE (conversation_id, sequence),
    UNIQUE (conversation_id, id)
);

CREATE TABLE summaries (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    version INTEGER NOT NULL CHECK (version > 0),
    first_sequence INTEGER NOT NULL,
    last_sequence INTEGER NOT NULL CHECK (last_sequence >= first_sequence),
    content_json TEXT NOT NULL CHECK (json_valid(content_json)),
    created_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id, first_sequence) REFERENCES messages(conversation_id, sequence),
    FOREIGN KEY (conversation_id, last_sequence) REFERENCES messages(conversation_id, sequence),
    UNIQUE (conversation_id, version),
    UNIQUE (conversation_id, first_sequence, last_sequence, content_json)
);

CREATE TABLE overviews (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL UNIQUE CHECK (version > 0),
    content_json TEXT NOT NULL CHECK (json_valid(content_json)),
    input_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE overview_sources (
    overview_id TEXT NOT NULL REFERENCES overviews(id),
    summary_id TEXT NOT NULL REFERENCES summaries(id),
    PRIMARY KEY (overview_id, summary_id)
);

CREATE TABLE profile_changes (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    source_message_id TEXT NOT NULL,
    base_version INTEGER NOT NULL CHECK (base_version >= 0),
    operations_json TEXT NOT NULL CHECK (json_valid(operations_json)),
    status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'rejected')),
    created_at TEXT NOT NULL,
    processed_at TEXT,
    applied_version INTEGER,
    FOREIGN KEY (conversation_id, source_message_id) REFERENCES messages(conversation_id, id),
    CHECK (
        (status = 'pending' AND processed_at IS NULL AND applied_version IS NULL) OR
        (status = 'rejected' AND processed_at IS NOT NULL AND applied_version IS NULL) OR
        (status = 'confirmed' AND processed_at IS NOT NULL AND applied_version IS NOT NULL)
    )
);

CREATE TABLE demo_seeds (
    name TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
