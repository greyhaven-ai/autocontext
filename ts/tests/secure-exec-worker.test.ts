import { describe, it, expect, afterEach } from "vitest";
import { SecureExecReplWorker } from "../src/rlm/secure-exec-worker.js";

const workers: SecureExecReplWorker[] = [];

afterEach(async () => {
  await Promise.all(workers.splice(0).map((worker) => worker.dispose()));
});

describe("SecureExecReplWorker", () => {
  it("persists namespace state across turns", async () => {
    const worker = new SecureExecReplWorker({
      namespace: { dataset: [1, 2, 3], answer: { ready: false, content: "" } },
    });
    workers.push(worker);

    const first = await worker.runCode({
      code: `
state.sum = dataset.reduce((total, value) => total + value, 0);
console.log(state.sum);
`,
    });

    expect(first.stdout).toContain("6");
    expect(first.error).toBeNull();

    const second = await worker.runCode({
      code: `
answer.ready = true;
answer.content = \`sum=\${state.sum}\`;
`,
    });

    expect(second.error).toBeNull();
    expect(second.answer.ready).toBe(true);
    expect(second.answer.content).toBe("sum=6");
  });

  it("denies filesystem access inside the sandbox", async () => {
    const worker = new SecureExecReplWorker();
    workers.push(worker);

    const result = await worker.runCode({
      code: `
const fs = require("fs");
console.log(fs.readFileSync("/etc/passwd", "utf8"));
`,
    });

    expect(result.error).toBeTruthy();
    expect(result.error).toMatch(/EACCES|ENOSYS|permission/i);
  });
});
