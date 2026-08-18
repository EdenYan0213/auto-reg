export function parseBooleanConfigValue(value: unknown, defaultValue = false): boolean {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value !== 0

  const normalized = String(value ?? '')
    .trim()
    .toLowerCase()

  if (normalized === '') return defaultValue

  return ['1', 'true', 'yes', 'on'].includes(normalized)
}
