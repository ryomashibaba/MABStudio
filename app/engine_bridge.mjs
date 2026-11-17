// App-owned minimal adapter. Actual architectural compilation is loaded from MAB.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

async function main() {
  const [, , command, engineRoot, input, output] = process.argv;
  if (command !== 'compile' || !engineRoot || !input || !output) throw Error('BAD_ARGS');
  const modulePath = path.resolve(engineRoot, 'source', 'ai', 'b143-building-dsl.mjs');
  if (!fs.existsSync(modulePath)) throw Error('B143_MODULE_MISSING');
  // All data come from app-private temp files. Not an arbitrary code executor.
  const {SCHEMA, validateDesign, compileDesign, verifyCompiled} = await import(pathToFileURL(modulePath).href);
  if (SCHEMA !== 'minecraftaibuilder-b143-building-dsl-v1' ||
      typeof validateDesign !== 'function' || typeof compileDesign !== 'function' ||
      typeof verifyCompiled !== 'function') throw Error('INCOMPATIBLE_MAB_ENGINE');
  const design = JSON.parse(fs.readFileSync(input, 'utf8'));
  if (!validateDesign(design)) throw Error('SOURCE_DESIGN_REJECTED');
  const scene = compileDesign(design);
  if (!verifyCompiled(design, scene)) throw Error('SOURCE_HASH_OR_GEOMETRY_MISMATCH');
  if (scene.designOnly !== true || scene.readiness !== 'NOT_PREPARED' ||
      scene.worldContacts !== 0 || scene.worldCoordinates !== null ||
      scene.stats?.constructionApproved !== false ||
      !Array.isArray(scene.BuildPlan?.operations) || scene.BuildPlan.operations.length !== 0) {
    throw Error('SOURCE_SAFETY_CONTRACT_CHANGED');
  }
  fs.writeFileSync(output, JSON.stringify(scene), {encoding: 'utf8', flag: 'wx'});
  process.stdout.write(JSON.stringify({ok:true,sourceSha256:scene.sourceSha256,voxels:scene.primitives.length}) + '\n');
}
main().catch(e => {process.stderr.write(String(e?.message || e) + '\n');process.exitCode=1;});
