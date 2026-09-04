export const delay = (ms: number) =>
  new Promise<void>((resolve) => window.setTimeout(resolve, ms));
