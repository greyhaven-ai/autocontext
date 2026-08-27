import { randomBytes } from "node:crypto";
import {
  closeSync,
  constants,
  existsSync,
  fsyncSync,
  lstatSync,
  mkdirSync,
  openSync,
  renameSync,
  readFileSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";

/** Read a private config file without following the final path as a symlink. */
export function readPrivateTextFile(configDir: string, filename: string): string | null {
  const filePath = join(configDir, filename);
  if (!existsSync(filePath)) return null;
  if (lstatSync(filePath).isSymbolicLink()) {
    throw new Error(`Refusing to read symbolic-link credential file: ${filePath}`);
  }

  const fileDescriptor = openSync(
    filePath,
    constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
  );
  try {
    return readFileSync(fileDescriptor, { encoding: "utf-8" });
  } finally {
    closeSync(fileDescriptor);
  }
}

/** Atomically replace a private config file without following file symlinks. */
export function writePrivateTextFile(
  configDir: string,
  filename: string,
  content: string,
): void {
  mkdirSync(configDir, { recursive: true });
  const filePath = join(configDir, filename);
  if (existsSync(filePath) && lstatSync(filePath).isSymbolicLink()) {
    throw new Error(`Refusing to replace symbolic-link credential file: ${filePath}`);
  }

  const tempPath = join(
    configDir,
    `.${filename}.${process.pid}.${randomBytes(8).toString("hex")}.tmp`,
  );
  let fileDescriptor: number | undefined;
  try {
    fileDescriptor = openSync(
      tempPath,
      constants.O_WRONLY |
        constants.O_CREAT |
        constants.O_EXCL |
        (constants.O_NOFOLLOW ?? 0),
      0o600,
    );
    writeFileSync(fileDescriptor, content, { encoding: "utf-8" });
    fsyncSync(fileDescriptor);
    closeSync(fileDescriptor);
    fileDescriptor = undefined;
    renameSync(tempPath, filePath);
  } finally {
    if (fileDescriptor !== undefined) closeSync(fileDescriptor);
    if (existsSync(tempPath)) unlinkSync(tempPath);
  }
}
