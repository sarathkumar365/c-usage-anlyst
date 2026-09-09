import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

type EnrollPayload = {
  schema_version?: number;
  collector_version?: string;
  org_id?: string;
  identity?: {
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
};

const DEFAULT_ORG_ID = "team-main";
const DEFAULT_INGEST_PATH = "/functions/v1/ingest";
const DEFAULT_SYNC_INTERVAL_MINUTES = 30;
const MAX_ENROLLMENTS_PER_IP_PER_DAY = 25;

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

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

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }
  if (req.method !== "POST") {
    return new Response("method not allowed", { status: 405, headers: corsHeaders });
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!supabaseUrl || !serviceRoleKey) {
    return new Response("server is not configured", { status: 500, headers: corsHeaders });
  }

  let payload: EnrollPayload;
  try {
    payload = await req.json();
  } catch {
    return new Response("invalid json", { status: 400, headers: corsHeaders });
  }

  const identity = payload.identity || {};
  if (!identity.collector_id || !identity.machine_id || !identity.user_id) {
    return new Response("invalid enrollment identity", { status: 400, headers: corsHeaders });
  }

  const supabase = createClient(supabaseUrl, serviceRoleKey, {
    auth: { persistSession: false },
  });

  const orgId = DEFAULT_ORG_ID;
  const ipHash = await sha256Hex(`${clientIp(req)}:${new Date().toISOString().slice(0, 10)}:${serviceRoleKey}`);
  const since = new Date();
  since.setUTCHours(0, 0, 0, 0);

  const { count, error: countError } = await supabase
    .from("collector_tokens")
    .select("token_hash", { count: "exact", head: true })
    .eq("enrollment_ip_hash", ipHash)
    .gte("enrolled_at", since.toISOString());
  if (countError) {
    return new Response(countError.message, { status: 500, headers: corsHeaders });
  }
  if ((count || 0) >= MAX_ENROLLMENTS_PER_IP_PER_DAY) {
    return new Response("daily enrollment limit reached", { status: 429, headers: corsHeaders });
  }

  const token = randomToken();
  const tokenHash = await sha256Hex(token);
  const labelParts = [
    identity.os_username || "unknown-user",
    identity.hostname || identity.collector_label || "unknown-host",
    new Date().toISOString(),
  ];

  await supabase
    .from("collector_tokens")
    .update({ revoked_at: new Date().toISOString() })
    .eq("org_id", orgId)
    .eq("collector_id", identity.collector_id)
    .is("revoked_at", null);

  const { error } = await supabase.from("collector_tokens").insert({
    token_hash: tokenHash,
    org_id: orgId,
    label: labelParts.join(" / "),
    collector_id: identity.collector_id,
    machine_id: identity.machine_id,
    user_id: identity.user_id,
    enrolled_at: new Date().toISOString(),
    enrollment_ip_hash: ipHash,
  });
  if (error) {
    return new Response(error.message, { status: 500, headers: corsHeaders });
  }

  return Response.json({
    ok: true,
    org_id: orgId,
    ingest_url: `${supabaseUrl}${DEFAULT_INGEST_PATH}`,
    collector_token: token,
    sync_interval_minutes: DEFAULT_SYNC_INTERVAL_MINUTES,
  }, { headers: corsHeaders });
});
