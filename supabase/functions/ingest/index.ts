// Ingest flow: authenticate token -> parse + validate payload -> skip duplicates -> write rows -> record run.
import { createClient, SupabaseClient } from "https://esm.sh/@supabase/supabase-js@2";

type Row = Record<string, unknown>;

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
    platform: Row;
  };
  claude: Row;
  summary: Row;
  daily: Row[];
  sessions: Row[];
  sources?: Row[];
  activity_daily?: Row[];
  anomalies: Array<{ code: string; severity: string; message: string }>;
};

type TokenRow = { org_id: string; collector_id: string | null; revoked_at: string | null };

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type, idempotency-key",
};

// Thrown by any step to end the request with this status and message.
class HttpError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

async function sha256Hex(value: string): Promise<string> {
  const data = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function check(error: { message: string } | null) {
  if (error) throw new HttpError(500, error.message);
}

async function authenticate(req: Request, supabase: SupabaseClient): Promise<{ tokenHash: string; tokenRow: TokenRow }> {
  const auth = req.headers.get("authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice("Bearer ".length).trim() : "";
  if (!token) throw new HttpError(401, "missing collector token");

  const tokenHash = await sha256Hex(token);
  const { data: tokenRow, error } = await supabase
    .from("collector_tokens")
    .select("org_id, collector_id, revoked_at")
    .eq("token_hash", tokenHash)
    .maybeSingle();
  check(error);
  if (!tokenRow || tokenRow.revoked_at) throw new HttpError(403, "invalid collector token");
  return { tokenHash, tokenRow };
}

async function parsePayload(req: Request, tokenRow: TokenRow): Promise<UsagePayload> {
  let payload: UsagePayload;
  try {
    payload = (await req.json()) as UsagePayload;
  } catch {
    throw new HttpError(400, "invalid json");
  }
  if (!payload?.identity?.collector_id || !payload?.identity?.machine_id || !payload?.identity?.user_id) {
    throw new HttpError(400, "invalid payload identity");
  }
  // Every usage table keys on collector_id, so binding it to the token stops one
  // token from overwriting another collector's rows. Legacy tokens have no collector_id.
  if (tokenRow.collector_id && tokenRow.collector_id !== payload.identity.collector_id) {
    throw new HttpError(403, "token does not belong to this collector");
  }
  if (!payload.idempotency_key) throw new HttpError(400, "missing idempotency key");
  return payload;
}

async function markDuplicateSeen(supabase: SupabaseClient, payload: UsagePayload, tokenHash: string): Promise<boolean> {
  const { data: existingRun } = await supabase
    .from("collector_runs")
    .select("idempotency_key")
    .eq("idempotency_key", payload.idempotency_key)
    .maybeSingle();
  if (!existingRun) return false;
  // Unchanged data still counts as a sync; the dashboard's last_sync_at reads received_at.
  const seenAt = new Date().toISOString();
  await supabase.from("collector_runs").update({ received_at: seenAt }).eq("idempotency_key", payload.idempotency_key);
  await supabase.from("collector_tokens").update({ last_used_at: seenAt }).eq("token_hash", tokenHash);
  return true;
}

async function writeUsage(supabase: SupabaseClient, orgId: string, payload: UsagePayload, now: string) {
  const identity = payload.identity;
  const scope = {
    org_id: orgId,
    collector_id: identity.collector_id,
    machine_id: identity.machine_id,
    user_id: identity.user_id,
  };
  const scoped = { ...scope, account_label: identity.account_label || "", updated_at: now };

  check((await supabase.from("machines").upsert({
    org_id: orgId,
    machine_id: identity.machine_id,
    hostname: identity.hostname,
    fqdn: identity.fqdn,
    platform: identity.platform || {},
    last_seen_at: now,
  })).error);

  check((await supabase.from("machine_users").upsert({
    org_id: orgId,
    user_id: identity.user_id,
    machine_id: identity.machine_id,
    os_username: identity.os_username,
    home_path_hash: identity.home_path_hash,
    last_seen_at: now,
  })).error);

  const tables: Array<[string, Row[] | undefined]> = [
    ["usage_daily", payload.daily],
    ["usage_sessions", payload.sessions],
    ["usage_sources", payload.sources],
    ["usage_activity_daily", payload.activity_daily],
  ];
  for (const [table, rows] of tables) {
    if (!Array.isArray(rows) || !rows.length) continue;
    // Server-owned identity columns are spread last so the payload cannot override them.
    check((await supabase.from(table).upsert(rows.map((row) => ({ ...row, ...scoped })))).error);
  }

  if (Array.isArray(payload.anomalies) && payload.anomalies.length) {
    check((await supabase.from("usage_anomalies").upsert(payload.anomalies.map((row) => ({
      ...scope,
      code: row.code,
      severity: row.severity,
      message: row.message,
      last_seen_at: now,
    })))).error);
  }
}

async function recordRun(supabase: SupabaseClient, orgId: string, payload: UsagePayload, tokenHash: string, now: string) {
  // Recorded last: a run row marks the key as done, so a failed write above must stay retryable.
  // The bulky arrays already live in the usage tables and are not duplicated here.
  const { daily: _daily, sessions: _sessions, sources: _sources, activity_daily: _activity, ...runPayload } = payload;
  const identity = payload.identity;
  const { error } = await supabase.from("collector_runs").insert({
    org_id: orgId,
    idempotency_key: payload.idempotency_key,
    collector_id: identity.collector_id,
    machine_id: identity.machine_id,
    user_id: identity.user_id,
    account_label: identity.account_label || "",
    collector_version: payload.collector_version,
    period_start: payload.period_start,
    period_end: payload.period_end,
    transcript_digest: payload.transcript_digest,
    summary: payload.summary || {},
    payload: runPayload,
  });
  if (error && error.code !== "23505") throw new HttpError(500, error.message);

  await supabase.from("collector_tokens").update({ last_used_at: now }).eq("token_hash", tokenHash);
}

async function handle(req: Request): Promise<Response> {
  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!supabaseUrl || !serviceRoleKey) throw new HttpError(500, "server is not configured");
  const supabase = createClient(supabaseUrl, serviceRoleKey, { auth: { persistSession: false } });

  const { tokenHash, tokenRow } = await authenticate(req, supabase);
  const payload = await parsePayload(req, tokenRow);
  if (await markDuplicateSeen(supabase, payload, tokenHash)) {
    return Response.json({ ok: true, duplicate: true }, { headers: corsHeaders });
  }

  const now = new Date().toISOString();
  await writeUsage(supabase, tokenRow.org_id, payload, now);
  await recordRun(supabase, tokenRow.org_id, payload, tokenHash, now);

  return Response.json({
    ok: true,
    duplicate: false,
    daily_rows: payload.daily?.length || 0,
    session_rows: payload.sessions?.length || 0,
    source_rows: payload.sources?.length || 0,
    activity_rows: payload.activity_daily?.length || 0,
    anomalies: payload.anomalies?.length || 0,
  }, { headers: corsHeaders });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  if (req.method !== "POST") return new Response("method not allowed", { status: 405, headers: corsHeaders });
  try {
    return await handle(req);
  } catch (err) {
    if (err instanceof HttpError) return new Response(err.message, { status: err.status, headers: corsHeaders });
    throw err;
  }
});
