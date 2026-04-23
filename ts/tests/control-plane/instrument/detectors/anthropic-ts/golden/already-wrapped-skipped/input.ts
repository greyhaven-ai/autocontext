import { Anthropic } from "@anthropic-ai/sdk";
import { instrumentClient } from "autoctx/integrations/anthropic";
const client = instrumentClient(new Anthropic());
