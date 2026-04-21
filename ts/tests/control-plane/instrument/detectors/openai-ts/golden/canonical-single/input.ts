import { OpenAI } from "openai";
const client = new OpenAI();
const response = await client.chat.completions.create({ model: "gpt-4o", messages: [] });
