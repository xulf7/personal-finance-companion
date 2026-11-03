CREATE TABLE archive_requests (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    first_sequence INTEGER NOT NULL,
    last_sequence INTEGER NOT NULL CHECK (last_sequence > first_sequence),
    profile_json TEXT NOT NULL CHECK (json_valid(profile_json)),
    status TEXT NOT NULL CHECK (status IN ('running','failed','succeeded')),
    attempt_id TEXT NOT NULL,
    lease_until TEXT NOT NULL,
    summary_id TEXT REFERENCES summaries(id),
    overview_id TEXT REFERENCES overviews(id),
    citations_json TEXT CHECK (citations_json IS NULL OR json_valid(citations_json)),
    candidate_count INTEGER NOT NULL DEFAULT 0 CHECK (candidate_count >= 0),
    model TEXT,
    usage_json TEXT CHECK (usage_json IS NULL OR json_valid(usage_json)),
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (conversation_id, first_sequence, last_sequence),
    FOREIGN KEY (conversation_id,first_sequence) REFERENCES messages(conversation_id,sequence),
    FOREIGN KEY (conversation_id,last_sequence) REFERENCES messages(conversation_id,sequence),
    CHECK ((status='succeeded' AND summary_id IS NOT NULL AND overview_id IS NOT NULL) OR
           (status!='succeeded' AND summary_id IS NULL AND overview_id IS NULL))
);
CREATE UNIQUE INDEX archive_one_unfinished ON archive_requests(conversation_id)
WHERE status!='succeeded';

CREATE TABLE archive_evidence (
    source_message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    archive_id TEXT NOT NULL REFERENCES archive_requests(id),
    change_id TEXT NOT NULL UNIQUE REFERENCES profile_changes(id),
    FOREIGN KEY (conversation_id,source_message_id) REFERENCES messages(conversation_id,id)
);
