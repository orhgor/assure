-- Clerk user ids are TEXT (user_xxx), not Supabase Auth UUIDs.
-- Do not add a foreign key to auth.users. Server uses the service role key.
-- Never store prompt text here. Encrypted API keys and credit counts only.

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

-- Atomic deduct after a successful Send. Returns false when the row is missing or balance < p_cost.
CREATE OR REPLACE FUNCTION public.spend_credit(p_user_id TEXT, p_cost INTEGER DEFAULT 1)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  current_balance INTEGER;
BEGIN
  SELECT balance INTO current_balance FROM credit_wallets WHERE user_id = p_user_id FOR UPDATE;
  IF current_balance IS NULL OR current_balance < p_cost THEN
    RETURN FALSE;
  END IF;
  UPDATE credit_wallets SET balance = balance - p_cost, updated_at = now() WHERE user_id = p_user_id;
  INSERT INTO credit_transactions (user_id, amount, type) VALUES (p_user_id, -p_cost, 'send');
  RETURN TRUE;
END;
$$;

-- Add credits (Pro checkout). Inserts a wallet if missing; never resets an existing balance.
CREATE OR REPLACE FUNCTION public.add_wallet_credits(
  p_user_id TEXT,
  p_amount INTEGER,
  p_type TEXT,
  p_tier TEXT DEFAULT NULL
)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  new_balance INTEGER;
BEGIN
  INSERT INTO credit_wallets (user_id, balance, tier, monthly_reset_date)
  VALUES (
    p_user_id,
    p_amount,
    COALESCE(p_tier, 'free'),
    now() + interval '30 days'
  )
  ON CONFLICT (user_id) DO UPDATE SET
    balance = credit_wallets.balance + EXCLUDED.balance,
    tier = COALESCE(p_tier, credit_wallets.tier),
    monthly_reset_date = CASE
      WHEN p_tier = 'pro' THEN now() + interval '30 days'
      ELSE credit_wallets.monthly_reset_date
    END,
    updated_at = now()
  RETURNING balance INTO new_balance;
  INSERT INTO credit_transactions (user_id, amount, type)
  VALUES (p_user_id, p_amount, p_type);
  RETURN new_balance;
END;
$$;

REVOKE ALL ON FUNCTION public.spend_credit(TEXT, INTEGER) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.add_wallet_credits(TEXT, INTEGER, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.spend_credit(TEXT, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION public.add_wallet_credits(TEXT, INTEGER, TEXT, TEXT) TO service_role;
