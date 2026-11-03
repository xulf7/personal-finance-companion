CREATE TABLE chat_requests (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    user_message_id TEXT NOT NULL UNIQUE,
    assistant_message_id TEXT UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('running', 'failed', 'succeeded')),
    attempt_id TEXT NOT NULL,
    lease_until TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    model TEXT,
    usage_json TEXT CHECK (usage_json IS NULL OR json_valid(usage_json)),
    FOREIGN KEY (conversation_id, user_message_id) REFERENCES messages(conversation_id, id),
    FOREIGN KEY (conversation_id, assistant_message_id) REFERENCES messages(conversation_id, id),
    CHECK ((status = 'succeeded' AND assistant_message_id IS NOT NULL) OR
           (status != 'succeeded' AND assistant_message_id IS NULL))
);

CREATE UNIQUE INDEX chat_one_unfinished ON chat_requests(conversation_id)
WHERE status != 'succeeded';
