-- Canonical copy: supabase/migrations/20260901153000_create_users.sql
-- Run that file in the Supabase SQL editor, or let GitHub Integration apply
-- supabase/migrations/ from this workbench repo (not the webpage branch).

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
