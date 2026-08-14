#!/usr/bin/env node

import { readdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { extname, relative, resolve } from "node:path";

const requireFromTypeScriptPackage = createRequire(
  new URL("../ts/package.json", import.meta.url),
);

let ts;
try {
  ts = requireFromTypeScriptPackage("typescript");
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(
    `Unable to load the TypeScript compiler from ts/node_modules. Run npm install in ts first.\n${message}\n`,
  );
  process.exit(2);
}

const requestedRoot = process.argv[2];
if (!requestedRoot) {
  process.stderr.write("Usage: node scripts/scan_typescript_imports.mjs <source-root>\n");
  process.exit(2);
}

const sourceRoot = resolve(requestedRoot);
const supportedExtensions = new Set([".ts", ".tsx", ".mts", ".cts"]);
const imports = [];

for (const path of sourceFiles(sourceRoot)) {
  const text = readFileSync(path, "utf8");
  const sourceFile = ts.createSourceFile(
    path,
    text,
    ts.ScriptTarget.Latest,
    true,
  );
  const parseDiagnostics = sourceFile.parseDiagnostics ?? [];
  if (parseDiagnostics.length > 0) {
    const diagnostic = parseDiagnostics[0];
    const position = diagnostic.start ?? 0;
    const { line, character } = sourceFile.getLineAndCharacterOfPosition(position);
    const message = ts.flattenDiagnosticMessageText(diagnostic.messageText, "\n");
    throw new Error(`${relative(sourceRoot, path)}:${line + 1}:${character + 1}: ${message}`);
  }

  const record = (node, moduleSpecifier) => {
    if (!isLiteralModuleSpecifier(moduleSpecifier)) return;
    const { line } = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
    imports.push({
      source: relative(sourceRoot, path).split("\\").join("/"),
      line: line + 1,
      imported: moduleSpecifier.text,
    });
  };

  const visit = (node) => {
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
      record(node, node.moduleSpecifier);
    } else if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword
    ) {
      record(node, node.arguments[0]);
    } else if (
      ts.isImportEqualsDeclaration(node) &&
      ts.isExternalModuleReference(node.moduleReference)
    ) {
      record(node, node.moduleReference.expression);
    } else if (
      ts.isImportTypeNode(node) &&
      ts.isLiteralTypeNode(node.argument)
    ) {
      record(node, node.argument.literal);
    }
    ts.forEachChild(node, visit);
  };

  visit(sourceFile);
}

imports.sort(
  (left, right) =>
    left.source.localeCompare(right.source) ||
    left.line - right.line ||
    left.imported.localeCompare(right.imported),
);
process.stdout.write(`${JSON.stringify(imports)}\n`);

function isLiteralModuleSpecifier(node) {
  return node !== undefined && ts.isStringLiteralLike(node);
}

function sourceFiles(root) {
  const files = [];
  const visitDirectory = (directory) => {
    const entries = readdirSync(directory, { withFileTypes: true }).sort((left, right) =>
      left.name.localeCompare(right.name),
    );
    for (const entry of entries) {
      const path = resolve(directory, entry.name);
      if (entry.isDirectory()) {
        visitDirectory(path);
      } else if (entry.isFile() && supportedExtensions.has(extname(entry.name))) {
        files.push(path);
      }
    }
  };
  visitDirectory(root);
  return files;
}
