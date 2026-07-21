#!/usr/bin/env node
// scripts/install-windsurf.mjs — deploy spec-superflow for Windsurf
// Skills → .windsurf/skills/, phase-guard rule → .windsurf/rules/phase-guard.md
import { installPlatform } from './lib/install.mjs';

installPlatform('windsurf').catch(err => {
  console.error(`❌ ${err.message}`);
  process.exit(1);
});
