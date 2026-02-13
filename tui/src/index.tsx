import React from "react";
import { render } from "ink";
import { App } from "./App.js";

const DEFAULT_URL = "ws://localhost:8000/ws/interactive";

function parseArgs(): { url: string } {
  const args = process.argv.slice(2);
  let url = DEFAULT_URL;

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === "--url" && i + 1 < args.length) {
      url = args[i + 1]!;
      i++;
    } else if (arg?.startsWith("--url=")) {
      url = arg.slice("--url=".length);
    }
  }

  return { url };
}

const { url } = parseArgs();

render(<App url={url} />);
