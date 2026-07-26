import fs from "node:fs";
import path from "node:path";

for (const folder of ["dist"]) {
  const target = path.resolve(process.cwd(), folder);
  fs.rmSync(target, { recursive: true, force: true });
}
