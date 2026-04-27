import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const repoRoot = join(import.meta.dirname, "..", "..");
const boundariesPath = join(repoRoot, "packages", "package-boundaries.json");

type TsCoreBoundary = {
	packagePath: string;
	tsconfigPath: string;
	exactIncludes: string[];
	blockedProgramPathSubstrings: string[];
	blockedPackageDependencies: string[];
};

type TsControlBoundary = {
	packagePath: string;
	tsconfigPath: string;
	exactIncludes: string[];
	blockedPackageDependencies: string[];
};

type PackageBoundaries = {
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

describe("package boundaries", () => {
	it("defines a shared package-boundary contract", () => {
		expect(existsSync(boundariesPath)).toBe(true);
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

		const output = execFileSync(
			join(repoRoot, "ts", "node_modules", ".bin", "tsc"),
			["-p", core.tsconfigPath, "--listFilesOnly"],
			{
				cwd: repoRoot,
				encoding: "utf-8",
			},
		);

		const fileList = output.split(/\r?\n/).filter(Boolean);
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
		}
	});
});
