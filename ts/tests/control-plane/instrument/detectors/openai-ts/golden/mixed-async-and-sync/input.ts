import { OpenAI } from "openai";
const primaryClient = new OpenAI();
const secondaryClient = new OpenAI({ baseURL: "https://alt-endpoint.example.com" });
