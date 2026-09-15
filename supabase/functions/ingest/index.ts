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
  accounts?: Row[];
  install?: Row;
  feature_usage?: Row[];
  plan_usage?: Row[];
};

// Payload arrays and the columns ingest may write from them. Unknown keys are dropped, so an older
// schema never fails an upsert because a newer collector sent an extra field.
type TableSpec = {
  table: string;
  rows: (payload: UsagePayload) => Row[] | undefined;
  columns: string[];
  // Payload columns that, with the identity columns, form the primary key.
  key: string[];
  accountLabel: boolean;
  stamp?: "updated_at" | "last_seen_at";
};

const TOKEN_COLUMNS = [
  "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
  "cache_5m_tokens", "cache_1h_tokens", "reported_total", "tool_calls",
];

const TABLES: TableSpec[] = [
  {
    table: "usage_daily",
    rows: (p) => p.daily,
    columns: ["day", "surface", "confidence", "project", "project_name", "model", "requests", "finalized_requests",
      "incomplete_requests", ...TOKEN_COLUMNS, "web_search_requests", "web_fetch_requests"],
    key: ["day", "project", "model"],
    accountLabel: true,
    stamp: "updated_at",
  },
  {
    table: "usage_sessions",
    rows: (p) => p.sessions,
    columns: ["session_id", "surface", "confidence", "project", "project_name", "is_subagent", "agent_id", "first_ts",
      "last_ts", "duration_seconds", "tokens_per_hour", "requests", "finalized_requests", "incomplete_requests", "models",
      ...TOKEN_COLUMNS, "git_branch", "entrypoints", "claude_code_version", "desktop_surface", "desktop_effort",
      "completed_turns", "active_spans"],
    key: ["session_id"],
    accountLabel: true,
    stamp: "updated_at",
  },
  {
    table: "usage_sources",
    rows: (p) => p.sources,
    columns: ["source_id", "surface", "status", "confidence", "path_hash", "file_count", "latest_activity_at", "extractor", "anomalies"],
    key: ["source_id"],
    accountLabel: true,
    stamp: "updated_at",
  },
  {
    table: "usage_activity_daily",
    rows: (p) => p.activity_daily,
    columns: ["day", "surface", "source_id", "sessions", "messages", "turns", "tool_calls", "reported_total", "confidence"],
    key: ["day", "surface", "source_id"],
    accountLabel: true,
    stamp: "updated_at",
  },
  {
    table: "usage_anomalies",
    rows: (p) => p.anomalies,
    columns: ["code", "severity", "message"],
    key: ["code"],
    accountLabel: false,
    stamp: "last_seen_at",
  },
  {
    table: "claude_accounts",
    rows: (p) => p.accounts,
    columns: ["account_uuid", "organization_uuid", "organization_name", "email_hash", "billing_type", "seat_tier",
      "user_rate_limit_tier", "organization_rate_limit_tier", "has_extra_usage", "source"],
    key: ["account_uuid"],
    accountLabel: false,
    stamp: "last_seen_at",
  },
  {
    table: "collector_features",
    rows: (p) => p.feature_usage,
    columns: ["kind", "name", "count"],
    key: ["kind", "name"],
    accountLabel: false,
    stamp: "updated_at",
  },
  {
    table: "plan_usage_samples",
    rows: (p) => p.plan_usage,
    columns: ["organization_uuid", "source", "sampled_at", "five_hour_pct", "seven_day_pct", "extra_usage",
      "five_hour_resets_at", "seven_day_resets_at"],
    key: ["organization_uuid", "source", "sampled_at"],
    accountLabel: false,
  },
];

type TokenRow = {
  org_id: string;
  collector_id: string | null;
  machine_id: string | null;
  user_id: string | null;
  revoked_at: string | null;
};

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
    .select("org_id, collector_id, machine_id, user_id, revoked_at")
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
  // Collectors before 0.6.1 re-derive machine_id from network names that change with the IP address,
  // so the IDs recorded at enrollment are authoritative; otherwise one machine splits into many.
  if (tokenRow.machine_id && tokenRow.user_id) {
    payload.identity.machine_id = tokenRow.machine_id;
    payload.identity.user_id = tokenRow.user_id;
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

  for (const spec of TABLES) {
    const rows = spec.rows(payload);
    if (!Array.isArray(rows) || !rows.length) continue;
    check((await supabase.from(spec.table).upsert(scopedRows(spec, rows, identity, orgId, now))).error);
  }
}

// Whitelisted columns plus server-owned identity, one row per primary key (a repeated key would fail the upsert).
function scopedRows(spec: TableSpec, rows: Row[], identity: UsagePayload["identity"], orgId: string, now: string): Row[] {
  const byKey = new Map<string, Row>();
  for (const row of rows) {
    const clean: Row = {};
    for (const column of spec.columns) if (column in row) clean[column] = row[column];
    Object.assign(clean, {
      org_id: orgId,
      collector_id: identity.collector_id,
      machine_id: identity.machine_id,
      user_id: identity.user_id,
      ...(spec.accountLabel ? { account_label: identity.account_label || "" } : {}),
      ...(spec.stamp ? { [spec.stamp]: now } : {}),
    });
    byKey.set(JSON.stringify(spec.key.map((column) => clean[column])), clean);
  }
  return [...byKey.values()];
}

async function recordRun(supabase: SupabaseClient, orgId: string, payload: UsagePayload, tokenHash: string, now: string) {
  // Recorded last: a run row marks the key as done, so a failed write above must stay retryable.
  // The bulky arrays already live in the usage tables and are not duplicated here.
  const {
    daily: _daily, sessions: _sessions, sources: _sources, activity_daily: _activity,
    accounts: _accounts, feature_usage: _features, plan_usage: _planUsage, ...runPayload
  } = payload;
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
    accounts: payload.accounts?.length || 0,
    plan_usage_samples: payload.plan_usage?.length || 0,
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
