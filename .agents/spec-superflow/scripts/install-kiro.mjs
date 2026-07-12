#!/usr/bin/env node
// scripts/install-kiro.mjs — deploy spec-superflow for Kiro
// Skills → .kiro/skills/, phase-guard rule → .kiro/steering/phase-guard.md
import { installPlatform } from './lib/install.mjs';

installPlatform('kiro').catch(err => {
  console.error(`❌ ${err.message}`);
  process.exit(1);
});
