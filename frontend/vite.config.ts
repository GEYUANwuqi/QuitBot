import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

export default defineConfig(({ command }) => {
  let host = "127.0.0.1";
  let port = 6699;
  if (command === "serve") {
    const configPath = resolve(
      root,
      process.env.QUITBOT_CONFIG || "config/accounts.json",
    );
    const webui = existsSync(configPath)
      ? JSON.parse(readFileSync(configPath, "utf8").replace(/^\uFEFF/, ""))
          .webui
      : undefined;
    host = webui?.host || host;
    port = Number(process.env.QUITBOT_MANAGEMENT_PORT || webui?.port || port);
    if (
      !host ||
      /[\s/]/.test(host) ||
      !Number.isInteger(port) ||
      port < 1024 ||
      port > 65535
    )
      throw new Error("WebUI 地址或端口无效，请检查运行配置");
  }
  const targetHost =
    host === "0.0.0.0" ? "127.0.0.1" : host === "::" ? "::1" : host;
  const target = `http://${targetHost.includes(":") ? `[${targetHost}]` : targetHost}:${port}`;
  const proxy = {
    "/ws": {
      target,
      ws: true,
    },
    "/accounts": {
      target,
      ws: true,
    },
  };
  return {
    plugins: [
      react(),
      {
        name: "third-party-licenses",
        apply: "build",
        generateBundle() {
          const notices = ["react", "react-dom", "scheduler", "lucide-react"].map(
            (name) => {
              const manifest = require.resolve(`${name}/package.json`);
              const { version } = JSON.parse(readFileSync(manifest, "utf8"));
              const license = readFileSync(resolve(dirname(manifest), "LICENSE"), "utf8");
              return `${name} ${version}\n\n${license}`;
            },
          );
          this.emitFile({
            type: "asset",
            fileName: "THIRD_PARTY_LICENSES.txt",
            source: notices.join("\n\n---\n\n"),
          });
        },
      },
    ],
    build: {
      outDir: resolve(root, "src/quitbot_backend/_webui"),
      emptyOutDir: true,
    },
    server: { port: 5173, strictPort: true, proxy },
    preview: { proxy },
  };
});
