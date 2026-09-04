export async function sha256Hex(value: BufferSource) {
  const hash = await crypto.subtle.digest("SHA-256", value);
  return Array.from(new Uint8Array(hash), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}
