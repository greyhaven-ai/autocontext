import { describe, test, expect } from "vitest";
import { parseExistingImports } from "../../../../src/control-plane/instrument/scanner/source-file.js";

describe("parseExistingImports — alias preservation (widened contract)", () => {
  test("python `from openai import OpenAI as Foo` records alias", () => {
    const result = parseExistingImports(
      ["from openai import OpenAI as Foo"],
      "python",
    );
    const openaiEntry = Array.from(result).find((e) => e.module === "openai");
    expect(openaiEntry).toBeDefined();
    const names = Array.from(openaiEntry!.names);
    expect(names).toContainEqual({ name: "OpenAI", alias: "Foo" });
  });

  test("python `from openai import OpenAI` records no alias", () => {
    const result = parseExistingImports(
      ["from openai import OpenAI"],
      "python",
    );
    const names = Array.from(Array.from(result).find((e) => e.module === "openai")!.names);
    expect(names).toContainEqual({ name: "OpenAI", alias: undefined });
  });

  test("python `import openai as oa` records alias", () => {
    const result = parseExistingImports(["import openai as oa"], "python");
    const names = Array.from(Array.from(result).find((e) => e.module === "openai")!.names);
    expect(names).toContainEqual({ name: "openai", alias: "oa" });
  });

  test("ts `import { OpenAI as Foo } from 'openai'` records alias", () => {
    const result = parseExistingImports(
      [`import { OpenAI as Foo } from "openai";`],
      "typescript",
    );
    const names = Array.from(Array.from(result).find((e) => e.module === "openai")!.names);
    expect(names).toContainEqual({ name: "OpenAI", alias: "Foo" });
  });

  test("ts `import * as openai from 'openai'` records alias", () => {
    const result = parseExistingImports(
      [`import * as openai from "openai";`],
      "typescript",
    );
    const names = Array.from(Array.from(result).find((e) => e.module === "openai")!.names);
    expect(names).toContainEqual({ name: "openai", alias: "openai" });
  });
});
