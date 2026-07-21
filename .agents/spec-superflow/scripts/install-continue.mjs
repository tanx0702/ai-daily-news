#!/usr/bin/env node
// scripts/install-continue.mjs — deploy spec-superflow for Continue
// Skills → .continue/skills/, phase-guard rule → .continue/rules/phase-guard.md
import { installPlatform } from './lib/install.mjs';

installPlatform('continue').catch(err => {
  console.error(`❌ ${err.message}`);
  process.exit(1);
});
