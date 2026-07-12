// ssf sync <change-dir> — merge delta specs into main specs with conflict detection
import { readFileSync, readdirSync, writeFileSync, existsSync, statSync, mkdirSync } from 'node:fs';
import path, { join, basename } from 'node:path';
import { validateSpecPathLayout } from './spec-paths.mjs';

function toPosix(value) {
  return value.replace(/\\/g, '/');
}

function pathApiFor(...values) {
  return values.some(value => value.includes('\\')) ? path.win32 : path.posix;
}

export function deriveCapabilityDir(changeSpecsDir, specFile) {
  const api = pathApiFor(changeSpecsDir, specFile);
  const relative = toPosix(api.relative(changeSpecsDir, specFile));
  return relative.replace(/\/spec\.md$/, '');
}

export async function run(args) {
  if (args.length < 1) {
    console.error('Usage: ssf sync <change-dir>');
    process.exit(2);
  }

  const changeDir = args[0];
  if (!existsSync(changeDir)) {
    console.error(`Error: "${changeDir}" not found`);
    process.exit(2);
  }

  const { Validator } = await import('../../dist/index.js');
  const validator = new Validator();

  // Collect all unsynced changes for conflict detection
  const changesDir = join(process.cwd(), 'changes');
  const allDeltas = [];

  if (existsSync(changesDir)) {
    for (const dir of readdirSync(changesDir)) {
      const dirPath = join(changesDir, dir);
      if (!statSync(dirPath).isDirectory()) continue;
      const layout = validateSpecPathLayout(dirPath, { requireSpecs: false });
      if (!layout.pass) {
        for (const failure of layout.failures) console.error(failure);
        process.exit(1);
      }
      if (layout.specFiles.length === 0) continue;

      for (const specFile of layout.specFiles) {
        const content = readFileSync(specFile, 'utf-8');
        allDeltas.push({ changeName: dir, content });
      }
    }
  }

  // Check for conflicts
  if (allDeltas.length > 0) {
    const conflictReport = validator.detectSyncConflicts(allDeltas);
    if (conflictReport.hasConflicts) {
      console.log('⚠️  Sync conflicts detected:\n');
      for (const conflict of conflictReport.conflicts) {
        console.log(`  Requirement: "${conflict.requirement}"`);
        console.log(`  Modified by: ${conflict.changes.join(', ')}\n`);
      }
      console.log('Resolve conflicts before syncing. Consider syncing changes one at a time.');
      process.exit(1);
    }
  }

  // Perform sync: copy delta specs to main specs/
  const changeSpecsDir = join(changeDir, 'specs');
  const mainSpecsDir = join(process.cwd(), 'specs');
  const changeName = basename(changeDir);
  const layout = validateSpecPathLayout(changeDir, { requireSpecs: true });
  if (!layout.pass) {
    for (const failure of layout.failures) console.error(failure);
    process.exit(1);
  }

  if (!existsSync(mainSpecsDir)) {
    mkdirSync(mainSpecsDir, { recursive: true });
  }

  let synced = 0;

  for (const specFile of layout.specFiles) {
    const capabilityDir = deriveCapabilityDir(changeSpecsDir, specFile);
    const targetDir = join(mainSpecsDir, capabilityDir);

    if (!existsSync(targetDir)) {
      mkdirSync(targetDir, { recursive: true });
    }

    const content = readFileSync(specFile, 'utf-8');
    writeFileSync(join(targetDir, 'spec.md'), content);
    console.log(`  📋 Synced: specs/${capabilityDir}/spec.md`);
    synced++;
  }

  console.log(`\n✅ Synced ${synced} spec(s) from ${changeName} to specs/`);
}
