/** Content-block flattening for Anthropic messages. */

export type ContentBlock = {
  type: string;
  text?: string;
  name?: string;
  input?: Record<string, unknown>;
  id?: string;
};

export function flattenContent(content: string | ContentBlock[]): string {
  if (typeof content === "string") return content;
  return content
    .filter((b) => b.type === "text")
    .map((b) => b.text ?? "")
    .join("");
}

export type ToolCall = { toolName: string; args: Record<string, unknown> };

export function extractToolUses(content: string | ContentBlock[]): ToolCall[] | null {
  if (typeof content === "string") return null;
  const result: ToolCall[] = content
    .filter((b) => b.type === "tool_use")
    .map((b) => ({
      toolName: b.name ?? "",
      args: (b.input ?? {}) as Record<string, unknown>,
    }));
  return result.length > 0 ? result : null;
}
