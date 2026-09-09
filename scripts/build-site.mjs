import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const dashboardHtml = await readFile(resolve("dashboard/index.html"), "utf8");
const serverPath = resolve("dist/server/index.js");

await mkdir(dirname(serverPath), { recursive: true });

const workerSource = `
const dashboardHtml = ${JSON.stringify(dashboardHtml)};

export default {
  async fetch() {
    return new Response(dashboardHtml, {
      headers: {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "no-store"
      }
    });
  }
};
`;

await writeFile(serverPath, workerSource.trimStart(), "utf8");
