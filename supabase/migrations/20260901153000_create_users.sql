-- Email and subscription tier only. No prompts, no API keys.
-- Server uses the Supabase secret key (bypasses RLS).
-- Anon/publishable must not read this table.

CREATE TABLE IF NOT EXISTS public.users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  tier TEXT DEFAULT 'free',
  stripe_customer_id TEXT,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.users FROM anon, authenticated;
GRANT ALL ON TABLE public.users TO service_role;
