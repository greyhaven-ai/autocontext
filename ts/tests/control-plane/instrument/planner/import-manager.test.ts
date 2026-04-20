/**
 * A2-I Layer 5 — import-manager unit tests (spec §6.2).
 *
 * Covers:
 *   - Python: placement after last `from __future__ import`, then after last
 *     existing import; new imports sorted alphabetically by module
 *   - TS/JS: placement after last top-level import; single vs double quote
 *     style detected from existing
 *   - Named vs default vs namespace per ImportSpec.kind
 *   - No-existing-imports: insertion after shebang / triple-slash / docstring
 *   - Deduplication by (module, name, alias, kind)
 *   - Extend existing same-module-same-kind group into a single statement
 */
import { describe, test, expect } from "vitest";
import { planImports } from "../../../../src/control-plane/instrument/planner/import-manager.js";
import { fromBytes } from "../../../../src/control-plane/instrument/scanner/source-file.js";
import type {
  ImportSpec,
  InstrumentLanguage,
  SourceFile,
} from "../../../../src/control-plane/instrument/contract/index.js";

function fileOf(path: string, language: InstrumentLanguage, content: string): SourceFile {
  return fromBytes({ path, language, bytes: Buffer.from(content, "utf-8") });
}

describe("planImports — Python placement", () => {
  test("inserts after last existing import; one blank line; sorted", () => {
    const sf = fileOf("x.py", "python", [
      "import os",
      "import sys",
      "",
      "x = 1",
      "",
    ].join("\n"));
    const specs: ImportSpec[] = [
      { module: "zlib", name: "compress", kind: "named" },
      { module: "abc", name: "ABC", kind: "named" },
    ];
    const plan = planImports({ sourceFile: sf, importsNeeded: specs });
    expect(plan.insertAt.line).toBe(3); // after `import sys` (line 2)
    expect(plan.statementSource).toContain("from abc import ABC");
    expect(plan.statementSource).toContain("from zlib import compress");
    // Alphabetical: abc before zlib.
    const abcIdx = plan.statementSource.indexOf("from abc");
    const zlibIdx = plan.statementSource.indexOf("from zlib");
    expect(abcIdx).toBeLessThan(zlibIdx);
    // Ends with blank line.
    expect(plan.statementSource.endsWith("\n\n")).toBe(true);
  });

  test("placement respects `from __future__ import` precedence", () => {
    const sf = fileOf("x.py", "python", [
      "from __future__ import annotations",
      "import os",
      "",
      "x = 1",
      "",
    ].join("\n"));
    const plan = planImports({
      sourceFile: sf,
      importsNeeded: [{ module: "abc", name: "ABC", kind: "named" }],
    });
    // Insert after "import os" (line 2), so line 3.
    expect(plan.insertAt.line).toBe(3);
  });

  test("no-existing-imports: insert after module docstring", () => {
    const sf = fileOf("x.py", "python", [
      '"""Module docstring."""',
      "",
      "x = 1",
      "",
    ].join("\n"));
    const plan = planImports({
      sourceFile: sf,
      importsNeeded: [{ module: "abc", name: "ABC", kind: "named" }],
    });
    // Docstring on line 1; insert after → line 2.
    expect(plan.insertAt.line).toBeGreaterThanOrEqual(2);
    expect(plan.statementSource).toContain("from abc import ABC");
  });

  test("no-existing-imports: insert after shebang", () => {
    const sf = fileOf("x.py", "python", [
      "#!/usr/bin/env python3",
      "x = 1",
      "",
    ].join("\n"));
    const plan = planImports({
      sourceFile: sf,
      importsNeeded: [{ module: "abc", name: "ABC", kind: "named" }],
    });
    expect(plan.insertAt.line).toBeGreaterThanOrEqual(2);
  });

  test("extends `from m import X, Y` — multiple specs from one module", () => {
    const sf = fileOf("x.py", "python", ["x = 1", ""].join("\n"));
    const specs: ImportSpec[] = [
      { module: "typing", name: "List", kind: "named" },
      { module: "typing", name: "Dict", kind: "named" },
    ];
    const plan = planImports({ sourceFile: sf, importsNeeded: specs });
    // Single `from typing import Dict, List` statement rather than two parallel.
    expect(plan.statementSource).toContain("from typing import Dict, List");
    // Only one occurrence of `from typing`.
    const matches = plan.statementSource.match(/from typing/g) ?? [];
    expect(matches.length).toBe(1);
  });

  test("deduplicates (module, name, alias, kind) across input", () => {
    const sf = fileOf("x.py", "python", ["x = 1", ""].join("\n"));
    const specs: ImportSpec[] = [
      { module: "abc", name: "ABC", kind: "named" },
      { module: "abc", name: "ABC", kind: "named" },
      { module: "abc", name: "ABC", kind: "named" },
    ];
    const plan = planImports({ sourceFile: sf, importsNeeded: specs });
    expect(plan.additionalSpecsEmitted).toHaveLength(1);
    expect(plan.statementSource).toContain("from abc import ABC");
  });

  test("filters specs already present in existingImports", () => {
    const sf = fileOf("x.py", "python", [
      "from abc import ABC",
      "x = 1",
      "",
    ].join("\n"));
    const specs: ImportSpec[] = [
      { module: "abc", name: "ABC", kind: "named" },
      { module: "zlib", name: "compress", kind: "named" },
    ];
    const plan = planImports({ sourceFile: sf, importsNeeded: specs });
    // abc.ABC already present → only zlib emitted.
    expect(plan.additionalSpecsEmitted).toHaveLength(1);
    expect(plan.additionalSpecsEmitted[0]!.module).toBe("zlib");
  });

  test("empty importsNeeded → empty statementSource", () => {
    const sf = fileOf("x.py", "python", ["x = 1", ""].join("\n"));
    const plan = planImports({ sourceFile: sf, importsNeeded: [] });
    expect(plan.statementSource).toBe("");
    expect(plan.additionalSpecsEmitted).toHaveLength(0);
  });
});

describe("planImports — TypeScript / JavaScript placement", () => {
  test("inserts after last import; named form", () => {
    const sf = fileOf("x.ts", "typescript", [
      'import { A } from "mod-a";',
      'import { B } from "mod-b";',
      "",
      "const x = 1;",
      "",
    ].join("\n"));
    const plan = planImports({
      sourceFile: sf,
      importsNeeded: [{ module: "mod-c", name: "C", kind: "named" }],
    });
    expect(plan.insertAt.line).toBe(3);
    expect(plan.statementSource).toContain('import { C } from "mod-c";');
  });

  test("matches single-quote style when majority of existing imports use single", () => {
    const sf = fileOf("x.ts", "typescript", [
      "import { A } from 'mod-a';",
      "import { B } from 'mod-b';",
      "",
      "const x = 1;",
      "",
    ].join("\n"));
    const plan = planImports({
      sourceFile: sf,
      importsNeeded: [{ module: "mod-c", name: "C", kind: "named" }],
    });
    expect(plan.statementSource).toContain("import { C } from 'mod-c';");
  });

  test("default import form", () => {
    const sf = fileOf("x.ts", "typescript", [
      'import { A } from "mod-a";',
      "",
      "const x = 1;",
      "",
    ].join("\n"));
    const plan = planImports({
      sourceFile: sf,
      importsNeeded: [{ module: "react", name: "React", kind: "default" }],
    });
    expect(plan.statementSource).toContain('import React from "react";');
  });

  test("namespace import form", () => {
    const sf = fileOf("x.ts", "typescript", ["const x = 1;", ""].join("\n"));
    const plan = planImports({
      sourceFile: sf,
      importsNeeded: [{ module: "lodash", name: "_", kind: "namespace" }],
    });
    expect(plan.statementSource).toContain('import * as _ from "lodash";');
  });

  test("extends `import { A, B } from \"mod\"` — multiple named specs", () => {
    const sf = fileOf("x.ts", "typescript", ["const x = 1;", ""].join("\n"));
    const specs: ImportSpec[] = [
      { module: "mod", name: "A", kind: "named" },
      { module: "mod", name: "B", kind: "named" },
    ];
    const plan = planImports({ sourceFile: sf, importsNeeded: specs });
    expect(plan.statementSource).toContain('import { A, B } from "mod";');
    const matches = plan.statementSource.match(/import { .* } from "mod"/g) ?? [];
    expect(matches.length).toBe(1);
  });

  test("no-existing-imports: insert at top", () => {
    const sf = fileOf("x.ts", "typescript", ["const x = 1;", ""].join("\n"));
    const plan = planImports({
      sourceFile: sf,
      importsNeeded: [{ module: "mod", name: "A", kind: "named" }],
    });
    expect(plan.insertAt.line).toBe(1);
  });

  test("JS commonjs side-effect imports are preserved in anchor computation", () => {
    const sf = fileOf("x.js", "javascript", [
      'import "side-effect-a";',
      'import "side-effect-b";',
      "",
      "const x = 1;",
    ].join("\n"));
    const plan = planImports({
      sourceFile: sf,
      importsNeeded: [{ module: "other", name: "O", kind: "named" }],
    });
    // After side-effect-b on line 2 → line 3.
    expect(plan.insertAt.line).toBe(3);
  });
});

describe("planImports — alphabetical ordering", () => {
  test("multiple Python imports sorted alphabetically by module", () => {
    const sf = fileOf("x.py", "python", ["x = 1", ""].join("\n"));
    const specs: ImportSpec[] = [
      { module: "zeta", name: "Z", kind: "named" },
      { module: "alpha", name: "A", kind: "named" },
      { module: "beta", name: "B", kind: "named" },
    ];
    const plan = planImports({ sourceFile: sf, importsNeeded: specs });
    const a = plan.statementSource.indexOf("from alpha");
    const b = plan.statementSource.indexOf("from beta");
    const z = plan.statementSource.indexOf("from zeta");
    expect(a).toBeLessThan(b);
    expect(b).toBeLessThan(z);
  });
});
