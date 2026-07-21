#!/usr/bin/env node
// scripts/install-pi.mjs — deploy spec-superflow for Pi agent
// Skills → .pi/skills/ (global: .pi/agent/skills/). No rules dir — invoke "/workflow-start" manually.
import { installPlatform } from './lib/install.mjs';

installPlatform('pi').catch(err => {
  console.error(`❌ ${err.message}`);
  process.exit(1);
});
