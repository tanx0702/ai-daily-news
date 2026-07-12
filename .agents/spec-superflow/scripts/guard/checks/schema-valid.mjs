// scripts/guard/checks/schema-valid.mjs — validate artifacts using the existing Validator engine
import fs from 'node:fs';
import path from 'node:path';
import { validateSpecPathLayout, relativeSpecPath } from '../../lib/spec-paths.mjs';

// Cached Validator instance, lazily loaded from dist/
let _Validator = null;

async function getValidator() {
  if (_Validator) return _Validator;
  try {
    const distPath = new URL('../../../dist/index.js', import.meta.url).pathname;
    const mod = await import(distPath);
    _Validator = mod.Validator;
    return _Validator;
  } catch (err) {
    throw new Error(`Failed to load Validator engine. Run 'npm run build' first. Original error: ${err.message}`);
  }
}

/**
 * Validate all artifacts in a change directory using the Validator engine.
 * Returns { pass, failures[] } — pass is true only if all artifacts are valid.
 */
export async function checkSchemaValid(changeDir) {
  const failures = [];
  const warnings = [];
  const Validator = await getValidator();
  const validator = new Validator();

  // Validate proposal.md
  const proposalPath = path.join(changeDir, 'proposal.md');
  if (fs.existsSync(proposalPath)) {
    const content = fs.readFileSync(proposalPath, 'utf-8');
    const changeName = path.basename(changeDir);
    const report = validator.validateChangeContent(changeName, content);
    for (const issue of report.issues) {
      if (issue.level === 'ERROR') {
        failures.push(`proposal.md: ${issue.message}`);
      } else if (issue.level === 'WARNING') {
        warnings.push(`proposal.md: ${issue.message}`);
      }
    }
  }

  const specLayout = validateSpecPathLayout(changeDir, { requireSpecs: true });
  failures.push(...specLayout.failures);

  for (const specFile of specLayout.specFiles) {
    const content = fs.readFileSync(specFile, 'utf-8');
    const report = validator.validateDeltaSpec(content);
    const rel = relativeSpecPath(changeDir, specFile);
    for (const issue of report.issues) {
      if (issue.level === 'ERROR') {
        failures.push(`${rel}: ${issue.message}`);
      } else if (issue.level === 'WARNING') {
        warnings.push(`${rel}: ${issue.message}`);
      }
    }
  }

  return { pass: failures.length === 0, failures, warnings };
}
