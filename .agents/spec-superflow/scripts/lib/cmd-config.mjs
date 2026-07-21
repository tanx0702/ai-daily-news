// ssf config — display or modify configuration
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { loadConfig, getDefaults, resolveModelProfile } from './config-loader.mjs';

export async function run(args) {
  const config = loadConfig(process.cwd());

  // No args → display full effective config
  if (args.length === 0) {
    console.log('Effective configuration:');
    console.log(JSON.stringify(config, null, 2));
    return;
  }

  if (args[0] === '--resolve-model') {
    if (!args[1]) {
      console.error('Usage: ssf config --resolve-model <profile>');
      process.exit(2);
    }
    try {
      console.log(JSON.stringify(resolveModelProfile(config, args[1])));
    } catch (error) {
      console.error(error.message);
      process.exit(1);
    }
    return;
  }

  // --get <path>
  if (args[0] === '--get' && args[1]) {
    const parts = args[1].split('.');
    let val = config;
    for (const p of parts) {
      if (val === undefined || val === null) { val = undefined; break; }
      val = val[p];
    }
    if (val !== undefined) {
      console.log(typeof val === 'object' ? JSON.stringify(val, null, 2) : val);
    } else {
      console.error(`Config path not found: ${args[1]}`);
      process.exit(1);
    }
    return;
  }

  // --set <path>=<value>
  if (args[0] === '--set' && args[1]) {
    const eqIdx = args[1].indexOf('=');
    if (eqIdx === -1) {
      console.error('Usage: ssf config [--get <path>] [--set <path>=<value>] [--resolve-model <profile>]');
      process.exit(2);
    }
    const path = args[1].slice(0, eqIdx);
    const rawValue = args[1].slice(eqIdx + 1);
    // Parse value: try number, boolean, then string
    let value;
    if (rawValue === 'true') value = true;
    else if (rawValue === 'false') value = false;
    else if (/^\d+$/.test(rawValue)) value = parseInt(rawValue, 10);
    else value = rawValue;

    // Load existing config file or start from empty
    const configPath = join(process.cwd(), 'spec-superflow.config.json');
    let fileConfig = {};
    if (existsSync(configPath)) {
      fileConfig = JSON.parse(readFileSync(configPath, 'utf-8'));
    }

    // Set the nested value
    const parts = path.split('.');
    let target = fileConfig;
    for (let i = 0; i < parts.length - 1; i++) {
      if (!target[parts[i]] || typeof target[parts[i]] !== 'object') {
        target[parts[i]] = {};
      }
      target = target[parts[i]];
    }
    target[parts[parts.length - 1]] = value;

    writeFileSync(configPath, JSON.stringify(fileConfig, null, 2) + '\n');
    console.log(`Set ${path} = ${JSON.stringify(value)}`);
    return;
  }

  console.error('Usage: ssf config [--get <path>] [--set <path>=<value>] [--resolve-model <profile>]');
  process.exit(2);
}
