export async function readStoredQueue<T>(
  key: string,
  isItem: (value: unknown) => value is T,
): Promise<T[]> {
  const values = (await chrome.storage.local.get(key))[key];
  return Array.isArray(values) ? values.filter(isItem) : [];
}

export async function replaceStoredQueue<T>(key: string, items: T[]) {
  await chrome.storage.local.set({ [key]: items });
}

export async function enqueueBounded<T>(
  key: string,
  item: T,
  isItem: (value: unknown) => value is T,
  identity: (value: T) => string,
  limit: number,
) {
  const itemId = identity(item),
    queue = (await readStoredQueue(key, isItem)).filter(
      (value) => identity(value) !== itemId,
    );
  await replaceStoredQueue(key, [...queue, item].slice(-limit));
}
