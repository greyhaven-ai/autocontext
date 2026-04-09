export function renderCodegenTemplate(
  template: string,
  replacements: Record<string, string>,
): string {
  const placeholders = [...new Set(template.match(/__[A-Z0-9_]+__/g) ?? [])];
  const unresolved = placeholders.filter((placeholder) => !(placeholder in replacements));

  if (unresolved.length > 0) {
    throw new Error(
      `Unresolved codegen placeholders: ${[...new Set(unresolved)].join(", ")}`,
    );
  }

  return template.replace(
    /__[A-Z0-9_]+__/g,
    (placeholder) => replacements[placeholder] ?? placeholder,
  );
}
