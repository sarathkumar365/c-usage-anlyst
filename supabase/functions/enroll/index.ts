// Enroll flow: verify enrollment secret -> validate identity -> rate limit -> rotate collector token.
import { createClient, SupabaseClient } from "https://esm.sh/@supabase/supabase-js@2";

type EnrollIdentity = {
  collector_id?: string;
  machine_id?: string;
  user_id?: string;
  os_username?: string;
  hostname?: string;
  fqdn?: string;
  collector_label?: string;
  account_label?: string;
  home_path_hash?: string;
  platform?: Record<string, unknown>;
};

type EnrollPayload = {
  schema_version?: number;
  collector_version?: string;
  org_id?: string;
  identity?: EnrollIdentity;
};

const DEFAULT_ORG_ID = "team-main";
const DEFAULT_INGEST_PATH = "/functions/v1/ingest";
const DEFAULT_SYNC_INTERVAL_MINUTES = 30;
const MAX_ENROLLMENTS_PER_IP_PER_DAY = 25;

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

function clientIp(req: Request): string {
  return (
    req.headers.get("cf-connecting-ip") ||
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    req.headers.get("x-real-ip") ||
    "unknown"
  );
}

async function parseIdentity(req: Request): Promise<Required<Pick<EnrollIdentity, "collector_id" | "machine_id" | "user_id">> & EnrollIdentity> {
  let payload: EnrollPayload;
  try {
    payload = await req.json();
  } catch {
    throw new HttpError(400, "invalid json");
  }
  const identity = payload.identity || {};
  if (!identity.collector_id || !identity.machine_id || !identity.user_id) {
    throw new HttpError(400, "invalid enrollment identity");
  }
  return identity as Required<Pick<EnrollIdentity, "collector_id" | "machine_id" | "user_id">> & EnrollIdentity;
}

async function verifySecret(supabase: SupabaseClient, orgId: string, secret: string) {
  const { data, error } = await supabase
    .from("enrollment_secrets")
    .select("secret_hash")
    .eq("org_id", orgId)
    .eq("secret_hash", await sha256Hex(secret))
    .is("revoked_at", null)
    .maybeSingle();
  if (error) throw new HttpError(500, error.message);
  if (!data) throw new HttpError(403, "invalid enrollment secret");
}

async function enforceRateLimit(supabase: SupabaseClient, ipHash: string) {
  const since = new Date();
  since.setUTCHours(0, 0, 0, 0);
  const { count, error } = await supabase
    .from("collector_tokens")
    .select("token_hash", { count: "exact", head: true })
    .eq("enrollment_ip_hash", ipHash)
    .gte("enrolled_at", since.toISOString());
  if (error) throw new HttpError(500, error.message);
  if ((count || 0) >= MAX_ENROLLMENTS_PER_IP_PER_DAY) throw new HttpError(429, "daily enrollment limit reached");
}

async function issueToken(
  supabase: SupabaseClient,
  orgId: string,
  identity: EnrollIdentity & { collector_id: string },
  ipHash: string,
): Promise<{ token: string; machineId: string; userId: string }> {
  const token = randomToken();
  const now = new Date().toISOString();

  // Re-enrolling keeps the IDs already on record: older collectors derive them from network names,
  // so a reinstall on another network would otherwise start a new machine and split its history.
  const { data: previous, error: previousError } = await supabase
    .from("collector_tokens")
    .select("machine_id, user_id")
    .eq("org_id", orgId)
    .eq("collector_id", identity.collector_id)
    .not("machine_id", "is", null)
    .not("user_id", "is", null)
    .order("enrolled_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (previousError) throw new HttpError(500, previousError.message);
  const machineId = previous?.machine_id || identity.machine_id;
  const userId = previous?.user_id || identity.user_id;

  // One active token per collector: re-enrolling revokes the previous one.
  await supabase
    .from("collector_tokens")
    .update({ revoked_at: now })
    .eq("org_id", orgId)
    .eq("collector_id", identity.collector_id)
    .is("revoked_at", null);

  const { error } = await supabase.from("collector_tokens").insert({
    token_hash: await sha256Hex(token),
    org_id: orgId,
    label: [identity.os_username || "unknown-user", identity.hostname || identity.collector_label || "unknown-host", now].join(" / "),
    collector_id: identity.collector_id,
    machine_id: machineId,
    user_id: userId,
    enrolled_at: now,
    enrollment_ip_hash: ipHash,
  });
  if (error) throw new HttpError(500, error.message);
  return { token, machineId, userId };
}

async function handle(req: Request): Promise<Response> {
  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!supabaseUrl || !serviceRoleKey) throw new HttpError(500, "server is not configured");

  const secret = req.headers.get("x-enrollment-secret") || "";
  if (!secret) throw new HttpError(401, "missing enrollment secret");
  const identity = await parseIdentity(req);

  const supabase = createClient(supabaseUrl, serviceRoleKey, { auth: { persistSession: false } });
  const orgId = DEFAULT_ORG_ID;
  await verifySecret(supabase, orgId, secret);

  const ipHash = await sha256Hex(`${clientIp(req)}:${new Date().toISOString().slice(0, 10)}:${serviceRoleKey}`);
  await enforceRateLimit(supabase, ipHash);
  const { token, machineId, userId } = await issueToken(supabase, orgId, identity, ipHash);

  return Response.json({
    ok: true,
    org_id: orgId,
    ingest_url: `${supabaseUrl}${DEFAULT_INGEST_PATH}`,
    collector_token: token,
    machine_id: machineId,
    user_id: userId,
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
    throw err;
  }
});
