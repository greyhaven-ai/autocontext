import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const repoRoot = join(import.meta.dirname, "..", "..");
const boundariesPath = join(repoRoot, "packages", "package-boundaries.json");
const productionTraceOpenContractSourcePaths = [
	"ts/src/production-traces/contract/generated-types.ts",
	"ts/src/production-traces/contract/branded-ids.ts",
	"ts/src/production-traces/contract/types.ts",
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

type TsPackageJson = {
	main: string;
	types: string;
	dependencies?: Record<string, string>;
	devDependencies?: Record<string, string>;
	peerDependencies?: Record<string, string>;
	optionalDependencies?: Record<string, string>;
	exports: {
		".": {
			import: string;
			types: string;
		};
	};
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
		productionTraces.typescriptOpenTaxonomy,
	];
}

function importSpecifiers(sourceText: string): string[] {
	return [...sourceText.matchAll(/(?:from|import)\s*["']([^"']+)["']/g)].map(
		(match) => match[1],
	);
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
			expect(
				existsSync(join(packageDir, packageJson.exports["."].import)),
			).toBe(true);
			expect(existsSync(join(packageDir, packageJson.exports["."].types))).toBe(
				true,
			);

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
