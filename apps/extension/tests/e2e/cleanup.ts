import { rm } from "node:fs/promises";
import { resolve } from "node:path";

export default async function cleanup() {
  await rm(resolve("tests/e2e/recruitment-e2e.db"), { force: true });
}
