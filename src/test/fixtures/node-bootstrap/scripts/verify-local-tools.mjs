import { realpathSync, writeFileSync } from "node:fs";
import { delimiter, relative, resolve } from "node:path";
import { spawnSync } from "node:child_process";

const projectRoot = process.cwd();
const localBin = resolve(projectRoot, "node_modules/.bin");
const commandPath = process.env.PATH?.split(delimiter) ?? [];
if (!commandPath.includes(localBin)) {
  throw new Error(`npm script omitted project-local tools: ${commandPath.join(delimiter)}`);
}

const vitest = realpathSync(resolve(localBin, "vitest"));
if (!vitest.startsWith(resolve(projectRoot, "node_modules") + "/")) {
  throw new Error(`vitest escaped project dependencies: ${vitest}`);
}
const version = spawnSync(resolve(localBin, "vitest"), ["--version"], {
  encoding: "utf8",
});
if (version.status !== 0 || !/^vitest\/3\./.test(version.stdout.trim())) {
  throw new Error(`unexpected project-local Vitest: ${version.stdout}${version.stderr}`);
}
writeFileSync(
  "local-tools.json",
  JSON.stringify({ vitest: version.stdout.trim(), target: relative(projectRoot, vitest) }) + "\n",
);
