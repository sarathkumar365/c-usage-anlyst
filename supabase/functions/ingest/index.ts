import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

type UsagePayload = {
  schema_version: number;
  collector_version: string;
  idempotency_key: string;
  transcript_digest: string;
  period_start: string | null;
  period_end: string | null;
  org_id?: string;
  identity: {
    collector_id: string;
    collector_label: string;
    account_label: string;
    machine_id: string;
    user_id: string;
    os_username: string;
    hostname: string;
    fqdn: string;
    home_path_hash: string;
    platform: Record<string, unknown>;
  };
  claude: Record<string, unknown>;
  summary: Record<string, unknown>;
  daily: Array<Record<string, unknown>>;
  sessions: Array<Record<string, unknown>>;
  anomalies: Array<{ code: string; severity: string; message: string }>;
};

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type, idempotency-key",
};

async function sha256Hex(value: string): Promise<string> {
  const data = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }
  if (req.method !== "POST") {
    return new Response("method not allowed", { status: 405, headers: corsHeaders });
  }

  const auth = req.headers.get("authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice("Bearer ".length).trim() : "";
  if (!token) {
    return new Response("missing collector token", { status: 401, headers: corsHeaders });
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!supabaseUrl || !serviceRoleKey) {
    return new Response("server is not configured", { status: 500, headers: corsHeaders });
  }

  const supabase = createClient(supabaseUrl, serviceRoleKey, {
    auth: { persistSession: false },
  });

  const tokenHash = await sha256Hex(token);
  const { data: tokenRow, error: tokenError } = await supabase
    .from("collector_tokens")
    .select("org_id, revoked_at")
    .eq("token_hash", tokenHash)
    .maybeSingle();

  if (tokenError) {
    return new Response(tokenError.message, { status: 500, headers: corsHeaders });
  }
  if (!tokenRow || tokenRow.revoked_at) {
    return new Response("invalid collector token", { status: 403, headers: corsHeaders });
  }

  const payload = (await req.json()) as UsagePayload;
  if (!payload?.identity?.collector_id || !payload?.identity?.machine_id || !payload?.identity?.user_id) {
    return new Response("invalid payload identity", { status: 400, headers: corsHeaders });
  }
  if (!payload.idempotency_key) {
    return new Response("missing idempotency key", { status: 400, headers: corsHeaders });
  }

  const orgId = tokenRow.org_id;
  const identity = payload.identity;
  const accountLabel = identity.account_label || "";

  const { data: existingRun } = await supabase
    .from("collector_runs")
    .select("idempotency_key")
    .eq("idempotency_key", payload.idempotency_key)
    .maybeSingle();
  if (existingRun) {
    return Response.json({ ok: true, duplicate: true }, { headers: corsHeaders });
  }

  const now = new Date().toISOString();

  await supabase.from("machines").upsert({
    org_id: orgId,
    machine_id: identity.machine_id,
    hostname: identity.hostname,
    fqdn: identity.fqdn,
    platform: identity.platform || {},
    last_seen_at: now,
  });

  await supabase.from("machine_users").upsert({
    org_id: orgId,
    user_id: identity.user_id,
    machine_id: identity.machine_id,
    os_username: identity.os_username,
    home_path_hash: identity.home_path_hash,
    last_seen_at: now,
  });

  await supabase.from("collector_runs").insert({
    org_id: orgId,
    idempotency_key: payload.idempotency_key,
    collector_id: identity.collector_id,
    machine_id: identity.machine_id,
    user_id: identity.user_id,
    account_label: accountLabel,
    collector_version: payload.collector_version,
    period_start: payload.period_start,
    period_end: payload.period_end,
    transcript_digest: payload.transcript_digest,
    summary: payload.summary || {},
    payload,
  });

  if (Array.isArray(payload.daily) && payload.daily.length) {
    const rows = payload.daily.map((row) => ({
      ...row,
      org_id: orgId,
      collector_id: identity.collector_id,
      machine_id: identity.machine_id,
      user_id: identity.user_id,
      account_label: accountLabel,
      updated_at: now,
    }));
    const { error } = await supabase.from("usage_daily").upsert(rows);
    if (error) return new Response(error.message, { status: 500, headers: corsHeaders });
  }

  if (Array.isArray(payload.sessions) && payload.sessions.length) {
    const rows = payload.sessions.map((row) => ({
      ...row,
      org_id: orgId,
      collector_id: identity.collector_id,
      machine_id: identity.machine_id,
      user_id: identity.user_id,
      account_label: accountLabel,
      updated_at: now,
    }));
    const { error } = await supabase.from("usage_sessions").upsert(rows);
    if (error) return new Response(error.message, { status: 500, headers: corsHeaders });
  }

  if (Array.isArray(payload.anomalies) && payload.anomalies.length) {
    const rows = payload.anomalies.map((row) => ({
      org_id: orgId,
      collector_id: identity.collector_id,
      machine_id: identity.machine_id,
      user_id: identity.user_id,
      code: row.code,
      severity: row.severity,
      message: row.message,
      last_seen_at: now,
    }));
    const { error } = await supabase.from("usage_anomalies").upsert(rows);
    if (error) return new Response(error.message, { status: 500, headers: corsHeaders });
  }

  await supabase
    .from("collector_tokens")
    .update({ last_used_at: now })
    .eq("token_hash", tokenHash);

  return Response.json({
    ok: true,
    duplicate: false,
    daily_rows: payload.daily?.length || 0,
    session_rows: payload.sessions?.length || 0,
    anomalies: payload.anomalies?.length || 0,
  }, { headers: corsHeaders });
});
