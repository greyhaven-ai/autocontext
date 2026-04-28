import { execFileSync } from "node:child_process";
import {
	cpSync,
	existsSync,
	mkdirSync,
	mkdtempSync,
	readFileSync,
	rmSync,
	writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { describe, expect, it } from "vitest";

const repoRoot = join(import.meta.dirname, "..", "..");
const boundariesPath = join(repoRoot, "packages", "package-boundaries.json");
const productionTraceOpenContractSourcePaths = [
	"ts/src/production-traces/contract/index.ts",
	"ts/src/production-traces/contract/generated-types.ts",
	"ts/src/production-traces/contract/branded-ids.ts",
	"ts/src/production-traces/contract/types.ts",
	"ts/src/production-traces/contract/canonical-json.ts",
	"ts/src/production-traces/contract/content-address.ts",
	"ts/src/production-traces/contract/factories.ts",
	"ts/src/production-traces/contract/invariants.ts",
	"ts/src/production-traces/contract/validators.ts",
];
const productionTraceOpenContractSchemaAssetPaths = [
	"ts/src/production-traces/contract/json-schemas/shared-defs.schema.json",
	"ts/src/production-traces/contract/json-schemas/trace-source.schema.json",
	"ts/src/production-traces/contract/json-schemas/session.schema.json",
	"ts/src/production-traces/contract/json-schemas/env-context.schema.json",
	"ts/src/production-traces/contract/json-schemas/timing-info.schema.json",
	"ts/src/production-traces/contract/json-schemas/usage-info.schema.json",
	"ts/src/production-traces/contract/json-schemas/production-outcome.schema.json",
	"ts/src/production-traces/contract/json-schemas/feedback-ref.schema.json",
	"ts/src/production-traces/contract/json-schemas/trace-links.schema.json",
	"ts/src/production-traces/contract/json-schemas/redaction-marker.schema.json",
	"ts/src/production-traces/contract/json-schemas/redaction-policy.schema.json",
	"ts/src/production-traces/contract/json-schemas/retention-policy.schema.json",
	"ts/src/production-traces/contract/json-schemas/production-trace.schema.json",
	"ts/src/production-traces/contract/json-schemas/selection-rule.schema.json",
	"ts/src/production-traces/contract/json-schemas/cluster-config.schema.json",
	"ts/src/production-traces/contract/json-schemas/rubric-config.schema.json",
	"ts/src/production-traces/contract/json-schemas/dataset-row.schema.json",
	"ts/src/production-traces/contract/json-schemas/dataset-manifest.schema.json",
];
const productionTraceOpenContractSourceIncludes =
	productionTraceOpenContractSourcePaths.map((entry) => `../../../${entry}`);
const productionTraceOpenContractSchemaAssetIncludes =
	productionTraceOpenContractSchemaAssetPaths.map((entry) => `../../../${entry}`);
const productionTraceOpenContractProgramPathSubstrings = [
	...productionTraceOpenContractSourcePaths,
	...productionTraceOpenContractSchemaAssetPaths,
].map((entry) => `/${entry}`);
const productionTraceOpenSdkSourcePaths = [
	"ts/src/production-traces/sdk/validate.ts",
	"ts/src/production-traces/sdk/build-trace.ts",
	"ts/src/production-traces/sdk/write-jsonl.ts",
	"ts/src/production-traces/sdk/trace-batch.ts",
];
const productionTraceOpenSdkSourceIncludes =
	productionTraceOpenSdkSourcePaths.map((entry) => `../../../${entry}`);
const productionTraceOpenSdkProgramPathSubstrings =
	productionTraceOpenSdkSourcePaths.map((entry) => `/${entry}`);

type TsCoreBoundary = {
	packagePath: string;
	tsconfigPath: string;
	exactIncludes: string[];
	blockedProgramPathSubstrings: string[];
	blockedPackageDependencies: string[];
	requiredPackageDependencies: string[];
};

type TsControlBoundary = {
	packagePath: string;
	tsconfigPath: string;
	exactIncludes: string[];
	blockedPackageDependencies: string[];
};

type LicensingGuardrails = {
	status: string;
	licenseMetadataIssue: string;
	rightsAuditIssue: string;
	forbiddenPathsUntilAC645: string[];
	typescriptPackageMetadata: {
		paths: string[];
		forbiddenPackageKeys: string[];
	};
};

type ProductionTraceSourceClaim = {
	coreOwnedSourceIncludes: string[];
	coreOwnedProgramPathSubstrings: string[];
};

type ProductionTraceOpenContractClaim = ProductionTraceSourceClaim & {
	coreOwnedSchemaAssetIncludes: string[];
	forbiddenImportPathSubstrings: string[];
	requiredPackageDependencies: string[];
};

type ProductionTraceBoundary = {
	typescriptOpenContract: ProductionTraceOpenContractClaim;
	typescriptOpenSdk: ProductionTraceOpenContractClaim;
	typescriptOpenTaxonomy: ProductionTraceSourceClaim;
};

type PackageBoundaries = {
	licensing: LicensingGuardrails;
	mixedDomains: {
		productionTraces: ProductionTraceBoundary;
	};
	typescript: {
		core: TsCoreBoundary;
		control: TsControlBoundary;
	};
};

type Topology = {
	typescript: {
		core: {
			path: string;
		};
		control: {
			path: string;
		};
	};
};

type TsPackageExport = {
	import: string;
	types: string;
};

type TsPackageJson = {
	main: string;
	types: string;
	dependencies?: Record<string, string>;
	devDependencies?: Record<string, string>;
	peerDependencies?: Record<string, string>;
	optionalDependencies?: Record<string, string>;
	exports: Record<string, TsPackageExport>;
};

function loadBoundaries(): PackageBoundaries {
	return JSON.parse(readFileSync(boundariesPath, "utf-8")) as PackageBoundaries;
}

function loadTopology(): Topology {
	return loadJson<Topology>(
		join(repoRoot, "packages", "package-topology.json"),
	);
}

function loadJson<T>(path: string): T {
	return JSON.parse(readFileSync(path, "utf-8")) as T;
}

function listTypeScriptProgramFiles(tsconfigPath: string): string[] {
	const output = execFileSync(
		join(repoRoot, "ts", "node_modules", ".bin", "tsc"),
		["-p", tsconfigPath, "--listFilesOnly"],
		{
			cwd: repoRoot,
			encoding: "utf-8",
		},
	);

	return output.split(/\r?\n/).filter(Boolean);
}

function listProductionTraceCoreClaims(
	productionTraces: ProductionTraceBoundary,
): ProductionTraceSourceClaim[] {
	return [
		productionTraces.typescriptOpenContract,
		productionTraces.typescriptOpenSdk,
		productionTraces.typescriptOpenTaxonomy,
	];
}

function importSpecifiers(sourceText: string): string[] {
	return [...sourceText.matchAll(/(?:from|import)\s*["']([^"']+)["']/g)].map(
		(match) => match[1],
	);
}

function resolveProductionTraceContractSpecifier(specifier: string): string {
	if (!specifier.startsWith("./") || !specifier.endsWith(".js")) {
		throw new Error(`Unexpected production-trace contract specifier: ${specifier}`);
	}
	return `ts/src/production-traces/contract/${specifier.slice(2, -3)}.ts`;
}

describe("package boundaries", () => {
	it("defines a shared package-boundary contract", () => {
		expect(existsSync(boundariesPath)).toBe(true);
	});

	it("keeps license metadata publication deferred to the blocking Linear issues", () => {
		const licensing = loadBoundaries().licensing;

		expect(licensing.status).toBe("deferred");
		expect(licensing.licenseMetadataIssue).toBe("AC-645");
		expect(licensing.rightsAuditIssue).toBe("AC-646");
	});

	it("keeps deferred license publication files absent", () => {
		const licensing = loadBoundaries().licensing;

		expect(licensing.forbiddenPathsUntilAC645).toEqual([
			"LICENSING.md",
			"packages/python/core/LICENSE",
			"packages/python/control/LICENSE",
			"packages/ts/core/LICENSE",
			"packages/ts/control-plane/LICENSE",
		]);
		for (const relativePath of licensing.forbiddenPathsUntilAC645) {
			expect(existsSync(join(repoRoot, relativePath))).toBe(false);
		}
	});

	it("keeps TypeScript package license metadata deferred for new package artifacts", () => {
		const metadata = loadBoundaries().licensing.typescriptPackageMetadata;

		expect(metadata.forbiddenPackageKeys).toEqual(["license"]);
		for (const relativePath of metadata.paths) {
			const packageJson = loadJson<Record<string, unknown>>(
				join(repoRoot, relativePath),
			);
			for (const key of metadata.forbiddenPackageKeys) {
				expect(packageJson).not.toHaveProperty(key);
			}
		}
	});

	it("reuses the topology path for the TypeScript core package", () => {
		const boundaries = loadBoundaries();
		const topology = loadTopology();

		expect(boundaries.typescript.core.packagePath).toBe(
			topology.typescript.core.path,
		);
	});

	it("reuses the topology path for the TypeScript control package", () => {
		const boundaries = loadBoundaries();
		const topology = loadTopology();

		expect(boundaries.typescript.control.packagePath).toBe(
			topology.typescript.control.path,
		);
	});

	it("requires exact include paths for the TypeScript core package", () => {
		const boundaries = loadBoundaries();
		const core = boundaries.typescript.core;
		const tsconfig = loadJson<{
			compilerOptions?: { noEmit?: boolean };
			include: string[];
		}>(join(repoRoot, core.tsconfigPath));

		expect(tsconfig.compilerOptions?.noEmit).toBe(false);
		expect(tsconfig.include).toEqual(core.exactIncludes);
		expect(tsconfig.include.every((entry) => !entry.includes("*"))).toBe(true);
	});

	it("claims only explicit production trace open contract sources in the TypeScript core package", () => {
		const boundaries = loadBoundaries();
		const core = boundaries.typescript.core;
		const productionTraces =
			boundaries.mixedDomains.productionTraces.typescriptOpenContract;

		expect(productionTraces.coreOwnedSourceIncludes).toEqual(
			productionTraceOpenContractSourceIncludes,
		);
		expect(productionTraces.coreOwnedSchemaAssetIncludes).toEqual(
			productionTraceOpenContractSchemaAssetIncludes,
		);
		expect(productionTraces.coreOwnedProgramPathSubstrings).toEqual(
			productionTraceOpenContractProgramPathSubstrings,
		);
		for (const sourceInclude of productionTraces.coreOwnedSourceIncludes) {
			expect(core.exactIncludes).toContain(sourceInclude);
		}
		for (const schemaAssetInclude of productionTraces.coreOwnedSchemaAssetIncludes) {
			expect(schemaAssetInclude).not.toContain("*");
			expect(
				existsSync(join(repoRoot, "packages", "ts", "core", schemaAssetInclude)),
			).toBe(true);
		}
	});

	it("claims production trace taxonomy as explicit TypeScript core-owned open vocabulary", () => {
		const boundaries = loadBoundaries();
		const core = boundaries.typescript.core;
		const productionTraces =
			boundaries.mixedDomains.productionTraces.typescriptOpenTaxonomy;

		expect(productionTraces.coreOwnedSourceIncludes).toEqual([
			"../../../ts/src/production-traces/taxonomy/anthropic-error-reasons.ts",
			"../../../ts/src/production-traces/taxonomy/openai-error-reasons.ts",
			"../../../ts/src/production-traces/taxonomy/index.ts",
		]);
		expect(productionTraces.coreOwnedProgramPathSubstrings).toEqual([
			"/ts/src/production-traces/taxonomy/anthropic-error-reasons.ts",
			"/ts/src/production-traces/taxonomy/openai-error-reasons.ts",
			"/ts/src/production-traces/taxonomy/index.ts",
		]);
		for (const sourceInclude of productionTraces.coreOwnedSourceIncludes) {
			expect(core.exactIncludes).toContain(sourceInclude);
		}
	});

	it("claims production trace SDK helpers as explicit TypeScript core-owned open SDK helpers", () => {
		const boundaries = loadBoundaries();
		const core = boundaries.typescript.core;
		const productionTraces =
			boundaries.mixedDomains.productionTraces.typescriptOpenSdk;

		expect(productionTraces.coreOwnedSourceIncludes).toEqual(
			productionTraceOpenSdkSourceIncludes,
		);
		expect(productionTraces.coreOwnedSchemaAssetIncludes).toEqual([]);
		expect(productionTraces.coreOwnedProgramPathSubstrings).toEqual(
			productionTraceOpenSdkProgramPathSubstrings,
		);
		for (const sourceInclude of productionTraces.coreOwnedSourceIncludes) {
			expect(core.exactIncludes).toContain(sourceInclude);
		}
	});

	it("exposes production trace SDK helpers through stable TypeScript core subpaths", () => {
		const boundaries = loadBoundaries();
		const core = boundaries.typescript.core;
		const packageJson = loadJson<TsPackageJson>(
			join(repoRoot, core.packagePath, "package.json"),
		);

		expect(packageJson.exports["./production-traces/validate"]).toEqual({
			import: "./dist/ts/src/production-traces/sdk/validate.js",
			types: "./dist/ts/src/production-traces/sdk/validate.d.ts",
		});
		expect(packageJson.exports["./production-traces/build-trace"]).toEqual({
			import: "./dist/ts/src/production-traces/sdk/build-trace.js",
			types: "./dist/ts/src/production-traces/sdk/build-trace.d.ts",
		});
		expect(packageJson.exports["./production-traces/write-jsonl"]).toEqual({
			import: "./dist/ts/src/production-traces/sdk/write-jsonl.js",
			types: "./dist/ts/src/production-traces/sdk/write-jsonl.d.ts",
		});
		expect(packageJson.exports["./production-traces/trace-batch"]).toEqual({
			import: "./dist/ts/src/production-traces/sdk/trace-batch.js",
			types: "./dist/ts/src/production-traces/sdk/trace-batch.d.ts",
		});
	});

	it("resolves build-trace SDK version from the emitted TypeScript core package", async () => {
		const boundaries = loadBoundaries();
		const core = boundaries.typescript.core;
		const packageDir = join(repoRoot, core.packagePath);
		const tempRoot = mkdtempSync(join(repoRoot, "ts", ".tmp-core-version-"));

		try {
			rmSync(join(packageDir, "dist"), { force: true, recursive: true });
			execFileSync("npm", ["run", "build"], {
				cwd: packageDir,
				encoding: "utf-8",
			});

			const installedCore = join(
				tempRoot,
				"node_modules",
				"@autocontext",
				"core",
			);
			mkdirSync(installedCore, { recursive: true });
			cpSync(join(packageDir, "dist"), join(installedCore, "dist"), {
				recursive: true,
			});
			writeFileSync(
				join(installedCore, "package.json"),
				JSON.stringify({
					name: "@autocontext/core",
					version: "9.8.7",
					type: "module",
				}),
			);

			const buildTraceModule = (await import(
				pathToFileURL(
					join(
						installedCore,
						"dist",
						"ts",
						"src",
						"production-traces",
						"sdk",
						"build-trace.js",
					),
				).href
			)) as typeof import("../src/production-traces/sdk/build-trace.js");
			const trace = buildTraceModule.buildTrace({
				provider: "openai",
				model: "gpt-4o-mini",
				messages: [
					{
						role: "user",
						content: "hi",
						timestamp: "2026-04-17T12:00:00.000Z",
					},
				],
				timing: {
					startedAt: "2026-04-17T12:00:00.000Z",
					endedAt: "2026-04-17T12:00:01.000Z",
					latencyMs: 1000,
				},
				usage: { tokensIn: 10, tokensOut: 5 },
				env: {
					environmentTag: "production",
					appId: "core-version-test",
				},
				traceId: "01HZ6X2K7M9A3B4C5D6E7F8G9H",
			} as Parameters<typeof buildTraceModule.buildTrace>[0]);

			expect(trace.source.sdk.version).toBe("9.8.7");
		} finally {
			rmSync(tempRoot, { force: true, recursive: true });
		}
	});

	it("keeps TypeScript production trace core ownership limited to explicit open claims", () => {
		const boundaries = loadBoundaries();
		const core = boundaries.typescript.core;
		const productionTraces = boundaries.mixedDomains.productionTraces;
		const ownedPathSubstrings = listProductionTraceCoreClaims(
			productionTraces,
		).flatMap((claim) => claim.coreOwnedProgramPathSubstrings);

		const fileList = listTypeScriptProgramFiles(core.tsconfigPath);
		const productionTraceFiles = fileList.filter((entry) =>
			entry.includes("/ts/src/production-traces/"),
		);
		expect(productionTraceFiles).toHaveLength(ownedPathSubstrings.length);
		for (const ownedPath of ownedPathSubstrings) {
			expect(
				productionTraceFiles.some((entry) => entry.includes(ownedPath)),
			).toBe(true);
		}
		for (const filePath of productionTraceFiles) {
			expect(
				ownedPathSubstrings.some((ownedPath) => filePath.includes(ownedPath)),
			).toBe(true);
		}
	});

	it("keeps production trace open contract sources independent of control-plane imports", () => {
		const productionTraces =
			loadBoundaries().mixedDomains.productionTraces.typescriptOpenContract;

		expect(productionTraces.forbiddenImportPathSubstrings).toEqual([
			"control-plane/",
		]);
		for (const sourceInclude of productionTraces.coreOwnedSourceIncludes) {
			const sourceText = readFileSync(
				join(repoRoot, "packages", "ts", "core", sourceInclude),
				"utf-8",
			);
			const imports = importSpecifiers(sourceText);
			for (const forbidden of productionTraces.forbiddenImportPathSubstrings) {
				expect(imports.some((specifier) => specifier.includes(forbidden))).toBe(
					false,
				);
			}
		}
	});

	it("keeps the production trace contract barrel limited to core-owned contract sources", () => {
		const contractBarrel = readFileSync(
			join(repoRoot, "ts", "src", "production-traces", "contract", "index.ts"),
			"utf-8",
		);
		const imports = importSpecifiers(contractBarrel);
		const importedPaths = [
			...new Set(imports.map(resolveProductionTraceContractSpecifier)),
		];

		expect(importedPaths).toEqual([
			"ts/src/production-traces/contract/branded-ids.ts",
			"ts/src/production-traces/contract/types.ts",
			"ts/src/production-traces/contract/validators.ts",
			"ts/src/production-traces/contract/canonical-json.ts",
			"ts/src/production-traces/contract/factories.ts",
			"ts/src/production-traces/contract/invariants.ts",
			"ts/src/production-traces/contract/content-address.ts",
		]);
		for (const importedPath of importedPaths) {
			expect(productionTraceOpenContractSourcePaths).toContain(importedPath);
		}
	});

	it("keeps production trace SDK JSONL serialization pointed at core-owned canonical JSON", () => {
		const productionTraces =
			loadBoundaries().mixedDomains.productionTraces.typescriptOpenContract;
		const writeJsonlSource = readFileSync(
			join(repoRoot, "ts", "src", "production-traces", "sdk", "write-jsonl.ts"),
			"utf-8",
		);
		const imports = importSpecifiers(writeJsonlSource);

		expect(productionTraces.coreOwnedSourceIncludes).toContain(
			"../../../ts/src/production-traces/contract/canonical-json.ts",
		);
		expect(imports).toContain("../contract/canonical-json.js");
		expect(imports).not.toContain(
			"../../control-plane/contract/canonical-json.js",
		);
	});

	it("preserves the control-plane canonical JSON path as a compatibility re-export", () => {
		const compatibilitySource = readFileSync(
			join(repoRoot, "ts", "src", "control-plane", "contract", "canonical-json.ts"),
			"utf-8",
		);
		const imports = importSpecifiers(compatibilitySource);

		expect(imports).toEqual([
			"../../production-traces/contract/canonical-json.js",
			"../../production-traces/contract/canonical-json.js",
		]);
	});

	it("keeps production trace SDK helpers independent of control-plane workflows", () => {
		const productionTraces =
			loadBoundaries().mixedDomains.productionTraces.typescriptOpenSdk;

		expect(productionTraces.forbiddenImportPathSubstrings).toEqual([
			"control-plane/",
			"../cli/",
			"../ingest/",
			"../dataset/",
			"../retention/",
			"../../traces/",
		]);
		for (const sourceInclude of productionTraces.coreOwnedSourceIncludes) {
			const sourceText = readFileSync(
				join(repoRoot, "packages", "ts", "core", sourceInclude),
				"utf-8",
			);
			const imports = importSpecifiers(sourceText);
			for (const forbidden of productionTraces.forbiddenImportPathSubstrings) {
				expect(imports.some((specifier) => specifier.includes(forbidden))).toBe(
					false,
				);
			}
		}
	});

	it("declares runtime dependencies needed by production trace open SDK sources", () => {
		const boundaries = loadBoundaries();
		const core = boundaries.typescript.core;
		const productionTraces =
			boundaries.mixedDomains.productionTraces.typescriptOpenSdk;
		const packageJson = loadJson<TsPackageJson>(
			join(repoRoot, core.packagePath, "package.json"),
		);
		const dependencies = packageJson.dependencies ?? {};

		expect(productionTraces.requiredPackageDependencies).toEqual(["ulid"]);
		for (const dependency of productionTraces.requiredPackageDependencies) {
			expect(Object.keys(dependencies)).toContain(dependency);
		}
	});

	it("declares runtime dependencies needed by production trace open contract sources", () => {
		const boundaries = loadBoundaries();
		const core = boundaries.typescript.core;
		const productionTraces =
			boundaries.mixedDomains.productionTraces.typescriptOpenContract;
		const packageJson = loadJson<TsPackageJson>(
			join(repoRoot, core.packagePath, "package.json"),
		);
		const dependencies = packageJson.dependencies ?? {};

		expect(productionTraces.requiredPackageDependencies).toEqual([
			"ulid",
			"ajv",
			"ajv-formats",
		]);
		for (const dependency of productionTraces.requiredPackageDependencies) {
			expect(Object.keys(dependencies)).toContain(dependency);
		}
	});

	it("keeps the TypeScript core package dependencies pointed away from control and umbrella packages", () => {
		const boundaries = loadBoundaries();
		const core = boundaries.typescript.core;
		const packageJson = loadJson<TsPackageJson>(
			join(repoRoot, core.packagePath, "package.json"),
		);
		const dependencySections = [
			packageJson.dependencies,
			packageJson.devDependencies,
			packageJson.peerDependencies,
			packageJson.optionalDependencies,
		];

		expect(core.blockedPackageDependencies).toEqual([
			"@autocontext/control-plane",
			"autoctx",
		]);
		for (const blockedPackage of core.blockedPackageDependencies) {
			for (const dependencies of dependencySections) {
				expect(Object.keys(dependencies ?? {})).not.toContain(blockedPackage);
			}
		}
	});

	it("declares runtime dependencies needed by the TypeScript core package root", () => {
		const boundaries = loadBoundaries();
		const core = boundaries.typescript.core;
		const packageJson = loadJson<TsPackageJson>(
			join(repoRoot, core.packagePath, "package.json"),
		);
		const dependencies = packageJson.dependencies ?? {};

		expect(core.requiredPackageDependencies).toEqual([
			"zod",
			"ulid",
			"ajv",
			"ajv-formats",
		]);
		for (const dependency of core.requiredPackageDependencies) {
			expect(Object.keys(dependencies)).toContain(dependency);
		}
	});

	it("requires exact include paths for the TypeScript control package", () => {
		const boundaries = loadBoundaries();
		const control = boundaries.typescript.control;
		const tsconfig = loadJson<{
			compilerOptions?: { noEmit?: boolean };
			include: string[];
		}>(join(repoRoot, control.tsconfigPath));

		expect(tsconfig.compilerOptions?.noEmit).toBe(false);
		expect(tsconfig.include).toEqual(control.exactIncludes);
		expect(tsconfig.include.every((entry) => !entry.includes("*"))).toBe(true);
	});

	it("keeps the TypeScript control package dependencies pointed away from the umbrella package", () => {
		const boundaries = loadBoundaries();
		const control = boundaries.typescript.control;
		const packageJson = loadJson<TsPackageJson>(
			join(repoRoot, control.packagePath, "package.json"),
		);
		const dependencySections = [
			packageJson.dependencies,
			packageJson.devDependencies,
			packageJson.peerDependencies,
			packageJson.optionalDependencies,
		];

		expect(control.blockedPackageDependencies).toEqual(["autoctx"]);
		for (const blockedPackage of control.blockedPackageDependencies) {
			for (const dependencies of dependencySections) {
				expect(Object.keys(dependencies ?? {})).not.toContain(blockedPackage);
			}
		}
	});

	it("keeps the TypeScript core program free of control-plane paths", () => {
		const boundaries = loadBoundaries();
		const core = boundaries.typescript.core;
		const fileList = listTypeScriptProgramFiles(core.tsconfigPath);

		for (const blocked of core.blockedProgramPathSubstrings) {
			expect(fileList.some((entry) => entry.includes(blocked))).toBe(false);
		}
	});

	it("builds package artifacts at the paths advertised by package.json", () => {
		const packages = [
			join(repoRoot, "packages", "ts", "core"),
			join(repoRoot, "packages", "ts", "control-plane"),
		];

		for (const packageDir of packages) {
			rmSync(join(packageDir, "dist"), { force: true, recursive: true });
			execFileSync("npm", ["run", "build"], {
				cwd: packageDir,
				encoding: "utf-8",
			});

			const packageJson = loadJson<TsPackageJson>(
				join(packageDir, "package.json"),
			);
			expect(existsSync(join(packageDir, packageJson.main))).toBe(true);
			expect(existsSync(join(packageDir, packageJson.types))).toBe(true);
			for (const packageExport of Object.values(packageJson.exports)) {
				expect(existsSync(join(packageDir, packageExport.import))).toBe(true);
				expect(existsSync(join(packageDir, packageExport.types))).toBe(true);
			}

			if (packageDir.endsWith(join("packages", "ts", "core"))) {
				const productionTraces =
					loadBoundaries().mixedDomains.productionTraces.typescriptOpenContract;
				for (const schemaAssetInclude of productionTraces.coreOwnedSchemaAssetIncludes) {
					const emittedPath = schemaAssetInclude.replace(
						/^\.\.\/\.\.\/\.\.\//,
						"",
					);
					expect(existsSync(join(packageDir, "dist", emittedPath))).toBe(true);
				}
			}
		}
	});
});
