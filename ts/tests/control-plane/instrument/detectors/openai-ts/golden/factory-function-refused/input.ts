import { OpenAI } from "openai";

function makeClient(): OpenAI {
  return new OpenAI();
}
