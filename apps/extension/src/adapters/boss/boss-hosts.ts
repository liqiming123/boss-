const ROOTS = ["zhipin.com", "bosszhipin.com"];

export function isBossHostname(hostname: string) {
  const normalized = hostname.toLowerCase();
  return ROOTS.some(
    (root) => normalized === root || normalized.endsWith(`.${root}`),
  );
}
