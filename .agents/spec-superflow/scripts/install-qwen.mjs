#!/usr/bin/env node
// scripts/install-qwen.mjs — deploy spec-superflow for Qwen Code
// Skills → .qwen/skills/, phase-guard rule → .qwen/rules/phase-guard.md
import { installPlatform } from './lib/install.mjs';

installPlatform('qwen').catch(err => {
  console.error(`❌ ${err.message}`);
  process.exit(1);
});
