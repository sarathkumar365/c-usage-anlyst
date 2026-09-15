// Enroll flow: rate limit -> verify enrollment secret -> authorize the collector ID -> rotate collector token.
import { createClient, SupabaseClient } from "https://esm.sh/@supabase/supabase-js@2.116.0";

type EnrollIdentity = {
  collector_id?: string;
  machine_id?: string;
  user_id?: string;
  os_username?: string;
  hostname?: string;
  collector_label?: string;
};

type EnrollPayload = {
  schema_version?: number;
  collector_version?: string;
  org_id?: string;
  identity?: EnrollIdentity;
};

type Identity = { collector_id: string; machine_id: string; user_id: string; os_username?: string; hostname?: string; collector_label?: string };

const DEFAULT_ORG_ID = "team-main";
const DEFAULT_INGEST_PATH = "/functions/v1/ingest";
const DEFAULT_SYNC_INTERVAL_MINUTES = 30;
const MAX_ENROLLMENTS_PER_IP_PER_DAY = 25;
const MAX_FAILED_ATTEMPTS_PER_IP_PER_HOUR = 10;
const MAX_BODY_BYTES = 16_000;
const ID_PATTERN = /^[A-Za-z0-9_.:-]{8,128}$/;

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type, x-enrollment-secret",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
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

function randomToken(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

// Database errors stay in the function logs; callers only learn that something failed.
function check(error: { message: string } | null, step: string) {
  if (error) {
    console.error(`enroll ${step}:`, error.message);
    throw new HttpError(500, "enrollment failed");
  }
}

// Cloudflare sets cf-connecting-ip in front of Supabase and overwrites any client-supplied value.
function clientIp(req: Request): string {
  return req.headers.get("cf-connecting-ip") || req.headers.get("x-real-ip") || "unknown";
}

function bearerToken(req: Request): string {
  const auth = req.headers.get("authorization") || "";
  return auth.startsWith("Bearer ") ? auth.slice("Bearer ".length).trim() : "";
}

function text(value: unknown, max = 128): string | undefined {
  return typeof value === "string" && value ? value.slice(0, max) : undefined;
}

async function parseIdentity(req: Request): Promise<Identity> {
  const body = await req.text();
  if (body.length > MAX_BODY_BYTES) throw new HttpError(413, "request too large");
  let payload: EnrollPayload;
  try {
    payload = JSON.parse(body);
  } catch {
    throw new HttpError(400, "invalid json");
  }
  const identity = payload?.identity || {};
  for (const key of ["collector_id", "machine_id", "user_id"] as const) {
    if (typeof identity[key] !== "string" || !ID_PATTERN.test(identity[key] as string)) {
      throw new HttpError(400, "invalid enrollment identity");
    }
  }
  return {
    collector_id: identity.collector_id as string,
    machine_id: identity.machine_id as string,
    user_id: identity.user_id as string,
    os_username: text(identity.os_username),
    hostname: text(identity.hostname),
    collector_label: text(identity.collector_label),
  };
}

async function enforceFailureLimit(supabase: SupabaseClient, ipHash: string) {
  const since = new Date(Date.now() - 60 * 60 * 1000).toISOString();
  const { count, error } = await supabase
    .from("enrollment_attempts")
    .select("ip_hash", { count: "exact", head: true })
    .eq("ip_hash", ipHash)
    .eq("succeeded", false)
    .gte("attempted_at", since);
  check(error, "failure count");
  if ((count || 0) >= MAX_FAILED_ATTEMPTS_PER_IP_PER_HOUR) throw new HttpError(429, "too many failed attempts");
}

async function recordAttempt(supabase: SupabaseClient, ipHash: string, succeeded: boolean) {
  const { error } = await supabase.from("enrollment_attempts").insert({ ip_hash: ipHash, succeeded });
  if (error) console.error("enroll attempt log:", error.message);
}

async function verifySecret(supabase: SupabaseClient, orgId: string, secret: string, ipHash: string) {
  const { data, error } = await supabase
    .from("enrollment_secrets")
    .select("secret_hash")
    .eq("org_id", orgId)
    .eq("secret_hash", await sha256Hex(secret))
    .is("revoked_at", null)
    .maybeSingle();
  check(error, "secret lookup");
  if (!data) {
    await recordAttempt(supabase, ipHash, false);
    throw new HttpError(403, "invalid enrollment secret");
  }
}

async function enforceEnrollmentLimit(supabase: SupabaseClient, ipHash: string) {
  const since = new Date();
  since.setUTCHours(0, 0, 0, 0);
  const { count, error } = await supabase
    .from("collector_tokens")
    .select("token_hash", { count: "exact", head: true })
    .eq("enrollment_ip_hash", ipHash)
    .gte("enrolled_at", since.toISOString());
  check(error, "enrollment count");
  if ((count || 0) >= MAX_ENROLLMENTS_PER_IP_PER_DAY) throw new HttpError(429, "daily enrollment limit reached");
}

// A collector ID that was enrolled before belongs to whoever holds its active token. Without that token the request
// is refused, so knowing someone's collector ID (and the shared secret) is not enough to take over their identity.
async function authorizeCollector(
  supabase: SupabaseClient,
  orgId: string,
  identity: Identity,
  presentedToken: string,
): Promise<{ machineId: string; userId: string }> {
  const { data: rows, error } = await supabase
    .from("collector_tokens")
    .select("token_hash, machine_id, user_id, revoked_at")
    .eq("org_id", orgId)
    .eq("collector_id", identity.collector_id);
  check(error, "collector lookup");
  if (!rows || rows.length === 0) return { machineId: identity.machine_id, userId: identity.user_id };

  const presentedHash = presentedToken ? await sha256Hex(presentedToken) : "";
  const owned = rows.find((row) => !row.revoked_at && row.token_hash === presentedHash);
  if (!owned) throw new HttpError(409, "collector already enrolled");
  return { machineId: owned.machine_id || identity.machine_id, userId: owned.user_id || identity.user_id };
}

async function issueToken(
  supabase: SupabaseClient,
  orgId: string,
  identity: Identity,
  ids: { machineId: string; userId: string },
  ipHash: string,
): Promise<string> {
  const token = randomToken();
  const now = new Date().toISOString();

  // One active token per collector: re-enrolling revokes the previous one.
  const { error: revokeError } = await supabase
    .from("collector_tokens")
    .update({ revoked_at: now })
    .eq("org_id", orgId)
    .eq("collector_id", identity.collector_id)
    .is("revoked_at", null);
  check(revokeError, "revoke");

  const { error } = await supabase.from("collector_tokens").insert({
    token_hash: await sha256Hex(token),
    org_id: orgId,
    label: [identity.os_username || "unknown-user", identity.hostname || identity.collector_label || "unknown-host", now].join(" / "),
    collector_id: identity.collector_id,
    machine_id: ids.machineId,
    user_id: ids.userId,
    enrolled_at: now,
    enrollment_ip_hash: ipHash,
  });
  check(error, "token insert");
  return token;
}

async function handle(req: Request): Promise<Response> {
  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!supabaseUrl || !serviceRoleKey) throw new HttpError(500, "server is not configured");
  const supabase = createClient(supabaseUrl, serviceRoleKey, { auth: { persistSession: false } });
  const orgId = DEFAULT_ORG_ID;
  const ipHash = await sha256Hex(`${clientIp(req)}:${new Date().toISOString().slice(0, 10)}:${serviceRoleKey}`);

  // Checked before the secret, so guessing the secret is limited too.
  await enforceFailureLimit(supabase, ipHash);
  const secret = req.headers.get("x-enrollment-secret") || "";
  if (!secret) {
    await recordAttempt(supabase, ipHash, false);
    throw new HttpError(401, "missing enrollment secret");
  }
  const identity = await parseIdentity(req);
  await verifySecret(supabase, orgId, secret, ipHash);
  await enforceEnrollmentLimit(supabase, ipHash);
  const ids = await authorizeCollector(supabase, orgId, identity, bearerToken(req));
  const token = await issueToken(supabase, orgId, identity, ids, ipHash);
  await recordAttempt(supabase, ipHash, true);

  return Response.json({
    ok: true,
    org_id: orgId,
    ingest_url: `${supabaseUrl}${DEFAULT_INGEST_PATH}`,
    collector_token: token,
    machine_id: ids.machineId,
    user_id: ids.userId,
    sync_interval_minutes: DEFAULT_SYNC_INTERVAL_MINUTES,
  }, { headers: corsHeaders });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  if (req.method !== "POST") return new Response("method not allowed", { status: 405, headers: corsHeaders });
  try {
    return await handle(req);
  } catch (err) {
    if (err instanceof HttpError) return new Response(err.message, { status: err.status, headers: corsHeaders });
    console.error("enroll unexpected:", err);
    return new Response("enrollment failed", { status: 500, headers: corsHeaders });
  }
});
