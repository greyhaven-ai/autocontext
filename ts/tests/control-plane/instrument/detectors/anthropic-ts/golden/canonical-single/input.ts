import { Anthropic } from "@anthropic-ai/sdk";
const client = new Anthropic();
const response = await client.messages.create({ model: "claude-opus-4-5", messages: [] });
