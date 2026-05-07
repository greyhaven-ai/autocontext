export function definedConfigOptions<T extends object>(opts: T): Partial<T> {
  return Object.fromEntries(
    Object.entries(opts).filter(([, value]) => value !== undefined),
  ) as Partial<T>;
}
