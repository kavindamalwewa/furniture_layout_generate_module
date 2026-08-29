import { spawn } from "node:child_process";

const command = process.platform === "win32" ? "python" : "python3";
const child = spawn(command, ["-m", "furniture_layout.web"], {
  cwd: process.cwd(),
  env: { ...process.env, PYTHONPATH: "src" },
  stdio: "inherit",
});

child.on("error", (error) => {
  console.error(`Could not start Python: ${error.message}`);
  console.error("Install Python 3.11+ and ensure it is available on PATH.");
  process.exit(1);
});

child.on("exit", (code, signal) => {
  process.exitCode = signal ? 1 : (code ?? 1);
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => child.kill(signal));
}
