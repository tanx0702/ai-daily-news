// Bundle: token-rules
// Collection of token efficiency lint rules for skill files.
// Each rule is exported individually; also exported as a default bundle.

const MAX_LINES_PER_SKILL = 250;
const MAX_CHARS_PER_SKILL = 10000;
const MAX_EMPHASIS_MARKERS = 30;
const MAX_IMPORTANT_OCCURRENCES = 3;
const MAX_CODE_BLOCK_LINES = 15;

function countEmphasisMarkers(content) {
  return (content.match(/\*\*[^*]+\*\*/g) || []).length;
}

function countImportant(content) {
  return (content.match(/\bIMPORTANT\b/g) || []).length;
}

function countExtremelyImportant(content) {
  return (content.match(/EXTREMELY_IMPORTANT/g) || []).length;
}

function countCritical(content) {
  return (content.match(/\bCRITICAL\b/g) || []).length;
}

function getCodeBlocks(content) {
  const blocks = [];
  const regex = /```[\s\S]*?```/g;
  let match;
  while ((match = regex.exec(content)) !== null) {
    const lines = match[0].split('\n').length;
    blocks.push({ content: match[0], lines });
  }
  return blocks;
}

/**
 * Check file line count does not exceed max.
 * @param {string} _skillName
 * @param {string} content
 * @param {object} _ctx
 * @param {number} [max=250]
 */
export async function checkMaxLines(_skillName, content, _ctx, max = MAX_LINES_PER_SKILL) {
  const lineCount = content.split('\n').length;
  if (lineCount > max) {
    return [{ severity: 'error', message: `File has ${lineCount} lines (max: ${max})` }];
  }
  return [];
}

/**
 * Check file character count does not exceed max.
 */
export async function checkMaxChars(_skillName, content, _ctx, max = MAX_CHARS_PER_SKILL) {
  const charCount = content.length;
  if (charCount > max) {
    return [{ severity: 'warning', message: `File has ${charCount} chars (max: ${max})` }];
  }
  return [];
}

/**
 * Check emphasis markers (**bold**) count does not exceed max.
 * Also checks for banned markers (EXTREMELY_IMPORTANT, CRITICAL).
 */
export async function checkMaxEmphasisMarkers(_skillName, content, _ctx, max = MAX_EMPHASIS_MARKERS) {
  const issues = [];
  const emphasis = countEmphasisMarkers(content);
  if (emphasis > max) {
    issues.push({ severity: 'warning', message: `File has ${emphasis} emphasis markers (max: ${max})` });
  }
  const ei = countExtremelyImportant(content);
  if (ei > 0) {
    issues.push({ severity: 'error', message: `File contains EXTREMELY_IMPORTANT (${ei}x) — banned marker` });
  }
  const cr = countCritical(content);
  if (cr > 0) {
    issues.push({ severity: 'error', message: `File contains CRITICAL (${cr}x) — banned marker` });
  }
  const imp = countImportant(content);
  if (imp > MAX_IMPORTANT_OCCURRENCES) {
    issues.push({ severity: 'warning', message: `File contains IMPORTANT ${imp}x (max: ${MAX_IMPORTANT_OCCURRENCES})` });
  }
  return issues;
}

/**
 * Check code blocks do not exceed max lines.
 */
export async function checkMaxCodeBlockLength(_skillName, content, _ctx, max = MAX_CODE_BLOCK_LINES) {
  const issues = [];
  const blocks = getCodeBlocks(content);
  blocks.forEach((block, i) => {
    if (block.lines > max) {
      issues.push({
        severity: 'warning',
        message: `Code block #${i + 1} has ${block.lines} lines (max: ${max})`,
      });
    }
  });
  return issues;
}

/**
 * Bundle: run all token rules and return combined issues.
 */
async function checkAll(skillName, content, ctx) {
  const results = await Promise.all([
    checkMaxLines(skillName, content, ctx),
    checkMaxChars(skillName, content, ctx),
    checkMaxEmphasisMarkers(skillName, content, ctx),
    checkMaxCodeBlockLength(skillName, content, ctx),
  ]);
  return results.flat();
}

export default {
  name: 'token-rules',
  check: checkAll,
};
