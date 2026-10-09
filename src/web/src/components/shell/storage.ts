const fallback = new Map<string, string>();
export function readStorage(key: string): string | null { return typeof localStorage === "undefined" ? fallback.get(key) ?? null : localStorage.getItem(key); }
export function writeStorage(key: string, value: string): void { if (typeof localStorage === "undefined") fallback.set(key, value); else localStorage.setItem(key, value); }
export function removeStorage(key: string): void { if (typeof localStorage === "undefined") fallback.delete(key); else localStorage.removeItem(key); }
