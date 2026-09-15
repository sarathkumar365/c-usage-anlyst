// Ingest flow: authenticate token -> parse + validate payload -> skip duplicates -> write rows -> record run.
import { createClient, SupabaseClient } from "https://esm.sh/@supabase/supabase-js@2.116.0";

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
  // Upper bound on rows per sync; a legitimate collector stays far below it.
  maxRows: number;
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
    maxRows: 20000,
    rows: (p) => p.daily,
    columns: ["day", "surface", "confidence", "project", "project_name", "model", "requests", "finalized_requests",
      "incomplete_requests", ...TOKEN_COLUMNS, "web_search_requests", "web_fetch_requests"],
    key: ["day", "project", "model"],
    accountLabel: true,
    stamp: "updated_at",
  },
  {
    table: "usage_sessions",
    maxRows: 10000,
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
    maxRows: 2000,
    rows: (p) => p.sources,
    columns: ["source_id", "surface", "status", "confidence", "path_hash", "file_count", "latest_activity_at", "extractor", "anomalies"],
    key: ["source_id"],
    accountLabel: true,
    stamp: "updated_at",
  },
  {
    table: "usage_activity_daily",
    maxRows: 5000,
    rows: (p) => p.activity_daily,
    columns: ["day", "surface", "source_id", "sessions", "messages", "turns", "tool_calls", "reported_total", "confidence"],
    key: ["day", "surface", "source_id"],
    accountLabel: true,
    stamp: "updated_at",
  },
  {
    table: "usage_anomalies",
    maxRows: 100,
    rows: (p) => p.anomalies,
    columns: ["code", "severity", "message"],
    key: ["code"],
    accountLabel: false,
    stamp: "last_seen_at",
  },
  {
    table: "claude_accounts",
    maxRows: 20,
    rows: (p) => p.accounts,
    columns: ["account_uuid", "organization_uuid", "organization_name", "email_hash", "billing_type", "seat_tier",
      "user_rate_limit_tier", "organization_rate_limit_tier", "has_extra_usage", "source"],
    key: ["account_uuid"],
    accountLabel: false,
    stamp: "last_seen_at",
  },
  {
    table: "collector_features",
    maxRows: 1000,
    rows: (p) => p.feature_usage,
    columns: ["kind", "name", "count"],
    key: ["kind", "name"],
    accountLabel: false,
    stamp: "updated_at",
  },
  {
    table: "plan_usage_samples",
    maxRows: 20000,
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
  last_used_at: string | null;
};

const MAX_BODY_BYTES = 5 * 1024 * 1024;
const MIN_SECONDS_BETWEEN_SYNCS = 10;
const MAX_TEXT_LENGTH = 256;
const MAX_JSON_LENGTH = 20_000;
const PERCENT_COLUMNS = new Set(["five_hour_pct", "seven_day_pct"]);
// Only these run fields are kept; the rest of the payload already lives in the usage tables or is not needed.
const STORED_RUN_FIELDS = ["schema_version", "collector_version", "summary", "drivers", "install", "anomalies", "period_start", "period_end"];

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

// Database errors stay in the function logs; callers only learn that the upload failed.
function check(error: { message: string } | null, step = "write") {
  if (error) {
    console.error(`ingest ${step}:`, error.message);
    throw new HttpError(500, "ingest failed");
  }
}

async function authenticate(req: Request, supabase: SupabaseClient): Promise<{ tokenHash: string; tokenRow: TokenRow }> {
  const auth = req.headers.get("authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice("Bearer ".length).trim() : "";
  if (!token) throw new HttpError(401, "missing collector token");

  const tokenHash = await sha256Hex(token);
  const { data: tokenRow, error } = await supabase
    .from("collector_tokens")
    .select("org_id, collector_id, machine_id, user_id, revoked_at, last_used_at")
    .eq("token_hash", tokenHash)
    .maybeSingle();
  check(error, "token lookup");
  // Tokens not bound to one collector could write as anyone, so only enrolled tokens are accepted.
  if (!tokenRow || tokenRow.revoked_at || !tokenRow.collector_id) throw new HttpError(403, "invalid collector token");
  if (tokenRow.last_used_at && Date.now() - new Date(tokenRow.last_used_at).getTime() < MIN_SECONDS_BETWEEN_SYNCS * 1000) {
    throw new HttpError(429, "syncing too often");
  }
  return { tokenHash, tokenRow };
}

async function parsePayload(req: Request, tokenRow: TokenRow): Promise<UsagePayload> {
  const body = await req.text();
  if (body.length > MAX_BODY_BYTES) throw new HttpError(413, "payload too large");
  let payload: UsagePayload;
  try {
    payload = JSON.parse(body) as UsagePayload;
  } catch {
    throw new HttpError(400, "invalid json");
  }
  if (!payload?.identity?.collector_id || !payload?.identity?.machine_id || !payload?.identity?.user_id) {
    throw new HttpError(400, "invalid payload identity");
  }
  // Every usage table keys on collector_id, so binding it to the token stops one
  // token from overwriting another collector's rows.
  if (tokenRow.collector_id !== payload.identity.collector_id) {
    throw new HttpError(403, "token does not belong to this collector");
  }
  // Collectors before 0.6.1 re-derive machine_id from network names that change with the IP address,
  // so the IDs recorded at enrollment are authoritative; otherwise one machine splits into many.
  if (tokenRow.machine_id && tokenRow.user_id) {
    payload.identity.machine_id = tokenRow.machine_id;
    payload.identity.user_id = tokenRow.user_id;
  }
  if (typeof payload.idempotency_key !== "string" || !payload.idempotency_key || payload.idempotency_key.length > 128) {
    throw new HttpError(400, "missing idempotency key");
  }
  for (const spec of TABLES) {
    const rows = spec.rows(payload);
    if (rows !== undefined && !Array.isArray(rows)) throw new HttpError(400, `invalid ${spec.table} rows`);
    if (Array.isArray(rows) && rows.length > spec.maxRows) throw new HttpError(413, `too many ${spec.table} rows`);
  }
  return payload;
}

async function markDuplicateSeen(supabase: SupabaseClient, orgId: string, payload: UsagePayload, tokenHash: string): Promise<boolean> {
  // Scoped to this token's collector, so replaying another collector's key cannot make it look freshly synced.
  const scope = (query: any) => query.eq("idempotency_key", payload.idempotency_key).eq("org_id", orgId).eq("collector_id", payload.identity.collector_id);
  const { data: existingRun } = await scope(supabase.from("collector_runs").select("idempotency_key")).maybeSingle();
  if (!existingRun) return false;
  // Unchanged data still counts as a sync; the dashboard's last_sync_at reads received_at.
  const seenAt = new Date().toISOString();
  await scope(supabase.from("collector_runs").update({ received_at: seenAt }));
  await supabase.from("collector_tokens").update({ last_used_at: seenAt }).eq("token_hash", tokenHash);
  return true;
}

async function writeUsage(supabase: SupabaseClient, orgId: string, payload: UsagePayload, now: string) {
  const identity = payload.identity;

  check((await supabase.from("machines").upsert({
    org_id: orgId,
    machine_id: identity.machine_id,
    hostname: cleanValue("hostname", identity.hostname),
    fqdn: "",
    platform: cleanValue("platform", identity.platform || {}),
    last_seen_at: now,
  })).error, "machines");

  check((await supabase.from("machine_users").upsert({
    org_id: orgId,
    user_id: identity.user_id,
    machine_id: identity.machine_id,
    os_username: cleanValue("os_username", identity.os_username),
    home_path_hash: cleanValue("home_path_hash", identity.home_path_hash),
    last_seen_at: now,
  })).error, "machine_users");

  for (const spec of TABLES) {
    const rows = spec.rows(payload);
    if (!Array.isArray(rows) || !rows.length) continue;
    check((await supabase.from(spec.table).upsert(scopedRows(spec, rows, identity, orgId, now))).error, spec.table);
  }
}

// Numbers must be finite and non-negative (percentages at most 100); text and JSON values are length-limited.
function cleanValue(column: string, value: unknown): unknown {
  if (typeof value === "number") {
    if (!Number.isFinite(value) || value < 0 || (PERCENT_COLUMNS.has(column) && value > 100)) {
      throw new HttpError(400, `invalid value for ${column}`);
    }
    return value;
  }
  if (typeof value === "string") return value.slice(0, MAX_TEXT_LENGTH);
  if (value !== null && typeof value === "object") {
    if (JSON.stringify(value).length > MAX_JSON_LENGTH) throw new HttpError(400, `value too large for ${column}`);
    return value;
  }
  return value;
}

// Whitelisted columns plus server-owned identity, one row per primary key (a repeated key would fail the upsert).
function scopedRows(spec: TableSpec, rows: Row[], identity: UsagePayload["identity"], orgId: string, now: string): Row[] {
  const byKey = new Map<string, Row>();
  for (const row of rows) {
    const clean: Row = {};
    if (row === null || typeof row !== "object") throw new HttpError(400, `invalid ${spec.table} row`);
    for (const column of spec.columns) if (column in row) clean[column] = cleanValue(column, row[column]);
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
  const runPayload: Row = {};
  for (const field of STORED_RUN_FIELDS) {
    const value = (payload as unknown as Row)[field];
    if (value !== undefined) runPayload[field] = cleanValue(field, value);
  }
  const identity = payload.identity;
  const { error } = await supabase.from("collector_runs").insert({
    org_id: orgId,
    idempotency_key: payload.idempotency_key,
    collector_id: identity.collector_id,
    machine_id: identity.machine_id,
    user_id: identity.user_id,
    account_label: identity.account_label || "",
    collector_version: cleanValue("collector_version", payload.collector_version),
    period_start: payload.period_start,
    period_end: payload.period_end,
    transcript_digest: cleanValue("transcript_digest", payload.transcript_digest),
    summary: cleanValue("summary", payload.summary || {}),
    payload: runPayload,
  });
  if (error && error.code !== "23505") check(error, "collector_runs");

  await supabase.from("collector_tokens").update({ last_used_at: now }).eq("token_hash", tokenHash);
}

async function handle(req: Request): Promise<Response> {
  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!supabaseUrl || !serviceRoleKey) throw new HttpError(500, "server is not configured");
  const supabase = createClient(supabaseUrl, serviceRoleKey, { auth: { persistSession: false } });

  if (Number(req.headers.get("content-length") || 0) > MAX_BODY_BYTES) throw new HttpError(413, "payload too large");
  const { tokenHash, tokenRow } = await authenticate(req, supabase);
  const payload = await parsePayload(req, tokenRow);
  if (await markDuplicateSeen(supabase, tokenRow.org_id, payload, tokenHash)) {
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
    // An unread upload keeps the runtime waiting for it until it times out, so rejected requests cancel theirs.
    if (!req.bodyUsed) await req.body?.cancel().catch(() => {});
    if (err instanceof HttpError) return new Response(err.message, { status: err.status, headers: corsHeaders });
    console.error("ingest unexpected:", err);
    return new Response("ingest failed", { status: 500, headers: corsHeaders });
  }
});
