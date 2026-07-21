// scripts/lib/platforms.mjs
// Platform registry for spec-superflow cross-platform installers.
//
// All paths verified against comet (https://github.com/rpamis/comet)
// src/core/platforms.ts and each platform's documented conventions.
// spec-superflow's guard mechanism is the phase-guard RULE file (auto-included
// by the platform), not a PreToolUse hook; therefore `supportsHooks`/
// `hookFormat` are recorded for documentation accuracy only — the shared
// installer does not write hook configs for these platforms (SessionStart
// wiring is deferred until each platform's session-start support is
// individually verified).

import { join } from 'node:path';

/**
 * @typedef {Object} PlatformConfig
 * @property {string} id              - platform identifier (cli arg / install-<id>)
 * @property {string} name            - human-readable name
 * @property {string} skillsDir       - project-local skills base dir (e.g. '.cline')
 * @property {string} [globalSkillsDir] - global (user-level) skills base dir
 * @property {string} [rulesBaseDir]  - base dir for rules; '' = project root;
 *                                      defaults to skillsDir when omitted
 * @property {string} [rulesDir]      - rules subdirectory (omit => no rules)
 * @property {'md'|'mdc'|'copilot'} [rulesFormat] - rule file format
 * @property {boolean} [supportsHooks]   - platform supports hooks (info only)
 * @property {string} [hookFormat]       - native hook config format (info only)
 * @property {string} [notes]            - install / usage notes
 */

/** Platforms added by the v0.8.13 platform-expansion PR. */
export const NEW_PLATFORMS = [
  {
    id: 'cline',
    name: 'Cline',
    skillsDir: '.cline',
    globalSkillsDir: '.cline',
    // Cline reads .clinerules/*.md at project root (not inside .cline/).
    rulesBaseDir: '',
    rulesDir: '.clinerules',
    rulesFormat: 'md',
    supportsHooks: false,
    notes: 'Cline auto-includes .clinerules/*.md as always-on context.',
  },
  {
    id: 'kiro',
    name: 'Kiro',
    skillsDir: '.kiro',
    globalSkillsDir: '.kiro',
    rulesDir: 'steering',
    rulesFormat: 'md',
    supportsHooks: true,
    hookFormat: 'kiro',
    notes: 'AWS Kiro IDE reads .kiro/steering/*.md as steering rules.',
  },
  {
    id: 'windsurf',
    name: 'Windsurf',
    skillsDir: '.windsurf',
    globalSkillsDir: '.windsurf',
    rulesDir: 'rules',
    rulesFormat: 'md',
    supportsHooks: true,
    hookFormat: 'windsurf',
    notes: 'Codeium Windsurf reads .windsurf/rules/*.md as rules.',
  },
  {
    id: 'qwen',
    name: 'Qwen Code',
    skillsDir: '.qwen',
    globalSkillsDir: '.qwen',
    rulesDir: 'rules',
    rulesFormat: 'md',
    supportsHooks: true,
    hookFormat: 'qwen',
    notes: 'Qwen Code CLI (Gemini CLI fork) reads .qwen/rules/*.md.',
  },
  {
    id: 'amazon-q',
    name: 'Amazon Q Developer',
    skillsDir: '.amazonq',
    globalSkillsDir: '.amazonq',
    rulesDir: 'rules',
    rulesFormat: 'md',
    supportsHooks: true,
    hookFormat: 'claude-code',
    notes: 'Amazon Q Developer CLI reads .amazonq/rules/*.md as rules.',
  },
  {
    id: 'roocode',
    name: 'Roo Code',
    skillsDir: '.roo',
    globalSkillsDir: '.roo',
    rulesDir: 'rules',
    rulesFormat: 'md',
    supportsHooks: false,
    notes: 'Roo Code (Roo Cline) reads .roo/rules/*.md as rules.',
  },
  {
    id: 'continue',
    name: 'Continue',
    skillsDir: '.continue',
    globalSkillsDir: '.continue',
    rulesDir: 'rules',
    rulesFormat: 'md',
    supportsHooks: false,
    notes: 'Continue (VS Code) reads .continue/rules/*.md as rules.',
  },
  {
    id: 'pi',
    name: 'Pi',
    skillsDir: '.pi',
    globalSkillsDir: '.pi/agent',
    // Pi has no rules directory — skills-only deployment.
    rulesDir: undefined,
    rulesFormat: undefined,
    supportsHooks: false,
    notes: 'Pi agent reads skills from .pi/ (global: .pi/agent/). No rules dir; invoke "/workflow-start" manually.',
  },
];

/** Lookup a new platform config by id. */
export function getPlatform(id) {
  const p = NEW_PLATFORMS.find(x => x.id === id);
  if (!p) {
    throw new Error(
      `Unknown platform: "${id}". Available: ${NEW_PLATFORMS.map(x => x.id).join(', ')}`,
    );
  }
  return p;
}

/** All platform ids added by this PR. */
export const NEW_PLATFORM_IDS = NEW_PLATFORMS.map(p => p.id);

/**
 * Compute the project-local rules target directory for a platform.
 * Mirrors comet: rules go to (rulesBaseDir || skillsDir)/rulesDir.
 * For Cline, rulesBaseDir='' places .clinerules/ at project root.
 */
export function rulesTargetDir(platform, targetRoot) {
  if (!platform.rulesDir) return null;
  const base = platform.rulesBaseDir !== undefined ? platform.rulesBaseDir : platform.skillsDir;
  // join(targetRoot, '', '.clinerules') === targetRoot/.clinerules
  return join(targetRoot, base, platform.rulesDir);
}

/** Phase-guard rule file name for a rules format. */
export function phaseGuardFileName(rulesFormat) {
  switch (rulesFormat) {
    case 'mdc': return 'phase-guard.mdc';
    case 'copilot': return 'spec-superflow.instructions.md';
    case 'md':
    default: return 'phase-guard.md';
  }
}
