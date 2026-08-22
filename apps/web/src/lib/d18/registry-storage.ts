/** Remove process-local source bytes before a registry is serialized. */
export function sanitizeRegistryForStorage<T extends Record<string, object>>(
  registry: T,
): Record<string, object> {
  return Object.fromEntries(
    Object.entries(registry).map(([id, entry]) => {
      const safe = { ...entry } as Record<string, unknown>;
      delete safe.pendingSourceMaterials;
      return [id, safe];
    }),
  );
}

export function pendingMaterialsAreComplete(
  expectedCount: number,
  actualCount: number,
): boolean {
  return expectedCount >= 0 && expectedCount === actualCount;
}
