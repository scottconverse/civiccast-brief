-- Sign-ups for new-episode emails. Email addresses are stored encrypted (AES-GCM, the row id as AAD).
CREATE TABLE subscriptions (
  id TEXT PRIMARY KEY,
  city TEXT NOT NULL,
  email_sealed TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending_confirmation', 'confirmed', 'unsubscribed')),
  created_at TEXT NOT NULL,
  confirm_sent_at TEXT,
  confirmed_at TEXT,
  unsubscribed_at TEXT
);
CREATE INDEX subscriptions_city_status ON subscriptions (city, status);

-- Sliding-window rate limit. Keys hold a keyed hash, never a raw address.
CREATE TABLE rate_hits (
  key TEXT NOT NULL,
  at INTEGER NOT NULL
);
CREATE INDEX rate_hits_key_at ON rate_hits (key, at);

-- The first feed read for a city sets the baseline: items already there are never emailed.
CREATE TABLE feed_state (
  city TEXT PRIMARY KEY,
  baseline_at TEXT NOT NULL
);

CREATE TABLE feed_items (
  city TEXT NOT NULL,
  guid TEXT NOT NULL,
  title TEXT NOT NULL,
  link TEXT NOT NULL,
  description TEXT NOT NULL,
  seen_at TEXT NOT NULL,
  PRIMARY KEY (city, guid)
);

CREATE TABLE deliveries (
  city TEXT NOT NULL,
  guid TEXT NOT NULL,
  subscription_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('sent', 'failed')),
  message_id TEXT,
  error TEXT,
  at TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (city, guid, subscription_id)
);
