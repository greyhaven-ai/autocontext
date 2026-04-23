import { Anthropic } from "@anthropic-ai/sdk";

function makeClient(): Anthropic {
  return new Anthropic();
}
