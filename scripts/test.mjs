import { spawn } from "node:child_process";

const command = process.platform === "win32" ? "python" : "python3";
const child = spawn(command, ["-m", "unittest", "discover", "-s", "tests", "-v"], {
  cwd: process.cwd(),
  env: { ...process.env, PYTHONPATH: "src" },
  stdio: "inherit",
});

child.on("error", (error) => {
  console.error(`Could not start tests: ${error.message}`);
  process.exit(1);
});

child.on("exit", (code) => {
  process.exitCode = code ?? 1;
});
