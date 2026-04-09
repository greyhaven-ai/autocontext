export function renderCodegenTemplate(
  template: string,
  replacements: Record<string, string>,
): string {
  let rendered = template;

  for (const [placeholder, value] of Object.entries(replacements)) {
    rendered = rendered.split(placeholder).join(value);
  }

  const unresolved = rendered.match(/__[A-Z0-9_]+__/g) ?? [];
  if (unresolved.length > 0) {
    throw new Error(
      `Unresolved codegen placeholders: ${[...new Set(unresolved)].join(", ")}`,
    );
  }

  return rendered;
}
