-- Canonical copy: supabase/migrations/20260901160000_credit_wallet.sql
-- Clerk user ids are TEXT. Do not reference auth.users.
-- Run that file in the Supabase SQL editor, or let GitHub Integration apply
-- supabase/migrations/ from this workbench repo (not the webpage branch).

CREATE TABLE IF NOT EXISTS public.user_settings (
  user_id TEXT PRIMARY KEY,
  encrypted_api_keys TEXT,
  preferences JSONB DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.credit_wallets (
  user_id TEXT PRIMARY KEY,
  balance INTEGER DEFAULT 100,
  tier TEXT DEFAULT 'free',
  monthly_reset_date TIMESTAMPTZ DEFAULT (now() + interval '30 days'),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.credit_transactions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id TEXT NOT NULL,
  amount INTEGER NOT NULL,
  type TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_credit_transactions_user_id ON public.credit_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_credit_transactions_created_at ON public.credit_transactions(created_at);

ALTER TABLE public.user_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.credit_wallets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.credit_transactions ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.user_settings FROM anon, authenticated;
REVOKE ALL ON TABLE public.credit_wallets FROM anon, authenticated;
REVOKE ALL ON TABLE public.credit_transactions FROM anon, authenticated;
GRANT ALL ON TABLE public.user_settings TO service_role;
GRANT ALL ON TABLE public.credit_wallets TO service_role;
GRANT ALL ON TABLE public.credit_transactions TO service_role;
