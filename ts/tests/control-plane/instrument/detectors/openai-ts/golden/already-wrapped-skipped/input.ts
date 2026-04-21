import { OpenAI } from "openai";
import { instrumentClient } from "autoctx/integrations/openai";
const client = instrumentClient(new OpenAI());
