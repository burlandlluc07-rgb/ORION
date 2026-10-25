// Approve on the phone app asks for the phone's fingerprint, face or screen lock (WebAuthn, platform
// authenticator). ORION checks the signature on the PC, so a tap alone can't approve. Deny never asks.
// WebAuthn needs an https page with a domain name (the Tailscale or Cloudflare link), not the http LAN address.
import { api, ApiError, IS_PHONE } from "./api";

const b64d = (s: string) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), (c) => c.charCodeAt(0));
const b64e = (b: ArrayBuffer) => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

interface Options { challenge: string; rp_id: string; user_id: string; user_name: string; credential_id: string | null }
export interface BiometricStatus { enabled: boolean; enrolled: boolean; rp_id: string }

export async function biometricSupported(): Promise<boolean> {
  if (!window.isSecureContext || !window.PublicKeyCredential) return false;
  try {
    return await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
  } catch {
    return false;
  }
}

async function enrol(): Promise<void> {
  const o = await api.post<Options>("/api/biometric/options", { purpose: "register" });
  const cred = (await navigator.credentials.create({
    publicKey: {
      challenge: b64d(o.challenge),
      rp: { id: o.rp_id, name: "ORION" },
      user: { id: b64d(o.user_id), name: o.user_name, displayName: o.user_name },
      pubKeyCredParams: [{ type: "public-key", alg: -7 }, { type: "public-key", alg: -257 }],
      authenticatorSelection: { authenticatorAttachment: "platform", userVerification: "required", residentKey: "discouraged" },
      attestation: "none",
      timeout: 60000,
    },
  })) as PublicKeyCredential | null;
  if (!cred) throw new Error("cancelled");
  const r = cred.response as AuthenticatorAttestationResponse;
  await api.post("/api/biometric/register", { clientDataJSON: b64e(r.clientDataJSON), attestationObject: b64e(r.attestationObject) });
}

async function sign(purpose: string) {
  const o = await api.post<Options>("/api/biometric/options", { purpose });
  if (!o.credential_id) throw new Error("no key");
  const cred = (await navigator.credentials.get({
    publicKey: {
      challenge: b64d(o.challenge),
      rpId: o.rp_id,
      allowCredentials: [{ type: "public-key", id: b64d(o.credential_id) }],
      userVerification: "required",
      timeout: 60000,
    },
  })) as PublicKeyCredential | null;
  if (!cred) throw new Error("cancelled");
  const r = cred.response as AuthenticatorAssertionResponse;
  return { id: cred.id, clientDataJSON: b64e(r.clientDataJSON), authenticatorData: b64e(r.authenticatorData), signature: b64e(r.signature) };
}

/** Will Approve on this phone ask for the fingerprint? (for the button's label) */
export async function biometricWillAsk(): Promise<boolean> {
  if (!IS_PHONE) return false;
  try {
    const st = await api.get<BiometricStatus>("/api/biometric");
    return st.enabled && (st.enrolled || (await biometricSupported()));
  } catch {
    return false;
  }
}

/** Send a decision. On the phone, Approve first asks for the fingerprint/face/screen lock where possible
 *  (the first time it also sets up the phone's key). Throws a friendly Error when the check fails. */
export async function sendDecision(path: string, purpose: string, decision: string): Promise<unknown> {
  if (!IS_PHONE || decision === "deny") return api.post(path, { decision });
  const st = await api.get<BiometricStatus>("/api/biometric").catch(() => null);
  const supported = await biometricSupported();
  try {
    if (st?.enabled && supported) {
      if (!st.enrolled) await enrol();
      return await api.post(path, { decision, assertion: await sign(purpose) });
    }
    return await api.post(path, { decision });
  } catch (e) {
    if (e instanceof ApiError && e.status === 403 && !supported) {
      throw new Error("Approve needs your fingerprint, and this page can't ask for it. Open ORION through its secure link, approve on your PC, or type “yes”.");
    }
    if (e instanceof DOMException || (e as Error).message === "cancelled") {
      throw new Error("Not approved: the fingerprint or screen-lock check was cancelled. It's still waiting.");
    }
    throw e;
  }
}
