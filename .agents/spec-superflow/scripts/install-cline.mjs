#!/usr/bin/env node
// scripts/install-cline.mjs — deploy spec-superflow for Cline
// Skills → .cline/skills/, phase-guard rule → .clinerules/phase-guard.md
import { installPlatform } from './lib/install.mjs';

installPlatform('cline').catch(err => {
  console.error(`❌ ${err.message}`);
  process.exit(1);
});
