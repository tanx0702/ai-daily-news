#!/usr/bin/env node
// scripts/install-roocode.mjs — deploy spec-superflow for Roo Code
// Skills → .roo/skills/, phase-guard rule → .roo/rules/phase-guard.md
import { installPlatform } from './lib/install.mjs';

installPlatform('roocode').catch(err => {
  console.error(`❌ ${err.message}`);
  process.exit(1);
});
